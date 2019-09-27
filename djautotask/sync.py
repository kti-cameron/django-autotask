import logging

from suds.client import Client
from atws.wrapper import AutotaskAPIException
from atws import wrapper, Query, helpers, picklist, connection
from requests.exceptions import ConnectTimeout, Timeout, ReadTimeout, SSLError
from io import BytesIO

from django.conf import settings
from django.db import transaction, IntegrityError
from django.utils import timezone
import requests
import suds.transport as transport

from djautotask import models
from djautotask.utils import DjautotaskSettings

logger = logging.getLogger(__name__)


class InvalidObjectException(Exception):
    """
    If for any reason an object can't be created (for example, it references
    an unknown foreign object, or is missing a required field), raise this
    so that the synchronizer can catch it and continue with other records.
    """
    pass


def log_sync_job(f):
    def wrapper(*args, **kwargs):
        sync_instance = args[0]
        created_count = updated_count = deleted_count = 0
        sync_job = models.SyncJob()
        sync_job.start_time = timezone.now()
        if sync_instance.full:
            sync_job.sync_type = 'full'
        else:
            sync_job.sync_type = 'partial'

        try:
            created_count, updated_count, deleted_count = f(*args, **kwargs)
            sync_job.success = True
        except Exception as e:
            sync_job.message = str(e.args[0])
            sync_job.success = False
            raise
        finally:
            sync_job.end_time = timezone.now()
            sync_job.entity_name = sync_instance.model_class.__name__
            sync_job.added = created_count
            sync_job.updated = updated_count
            sync_job.deleted = deleted_count
            sync_job.save()

        return created_count, updated_count, deleted_count

    return wrapper


class SyncResults:
    """Track results of a sync job."""

    def __init__(self):
        self.created_count = 0
        self.updated_count = 0
        self.deleted_count = 0
        self.synced_ids = set()


class CustomRequestsTransport(transport.Transport):
    # Adapted from atws.connection.RequestsTransport so that we can set
    # our own request settings.

    def __init__(self, session, request_settings):
        transport.Transport.__init__(self)

        self._session = session
        self.timeout = (request_settings.get('timeout'))
        self.max_attempts = request_settings.get('max_attempts')

    def open(self, request):
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self._session.get(request.url, timeout=self.timeout)
                break
            except (SSLError, ConnectTimeout, Timeout, ReadTimeout) as e:
                if attempt == self.max_attempts:
                    raise AutotaskAPIException
                logger.error(
                    'Connection error. The error was: {}'.format(e)
                )
                continue
        return BytesIO(resp.content)

    def send(self, request):
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self._session.post(
                    request.url,
                    data=request.message,
                    headers=request.headers,
                    timeout=self.max_attempts
                )
                break
            except (SSLError, Timeout) as e:
                if attempt == self.max_attempts:
                    raise
                logger.error(
                    'Connection error. The error was: {}'.format(e)
                )
                continue
        return transport.Reply(
            resp.status_code,
            resp.headers,
            resp.content,
        )


class Synchronizer:
    lookup_key = 'id'

    def __init__(self, full=False, *args, **kwargs):
        self.full = full
        self.request_settings = DjautotaskSettings().get_settings()
        self.at_api_client = self.init_api_connection()

    def init_api_connection(self, **kwargs):

        client_options = kwargs.setdefault('client_options', {})

        kwargs['apiversion'] = settings.AUTOTASK_CREDENTIALS['api_version']
        kwargs['integrationcode'] = \
            settings.AUTOTASK_CREDENTIALS['integration_code']
        kwargs['url'] = settings.AUTOTASK_CREDENTIALS['url']

        session = requests.Session()
        session.auth = (
            settings.AUTOTASK_CREDENTIALS['username'],
            settings.AUTOTASK_CREDENTIALS['password']
        )
        session.mount(
            'https://',
            requests.adapters.HTTPAdapter(
                max_retries=self.request_settings.get('max_attempts'))
        )
        client_options['transport'] = \
            CustomRequestsTransport(session, self.request_settings)

        url = connection.get_connection_url(**kwargs)
        client_options['url'] = url

        return wrapper.Wrapper(**kwargs)

    def set_relations(self, instance, object_data):
        for object_field, value in self.related_meta.items():
            model_class, field_name = value
            self._assign_relation(
                instance,
                object_data,
                object_field,
                model_class,
                field_name
            )

    def _assign_relation(self, instance, object_data,
                         object_field, model_class, field_name):

        relation_id = object_data.get(object_field)
        try:
            related_instance = model_class.objects.get(pk=relation_id)
            setattr(instance, field_name, related_instance)
        except model_class.DoesNotExist:
            logger.warning(
                'Failed to find {} {} for {} {}.'.format(
                    object_field, relation_id, type(instance), instance.id
                )
            )

    def _instance_ids(self, filter_params=None):
        if not filter_params:
            ids = self.model_class.objects.all().values_list('id', flat=True)
        else:
            ids = self.model_class.objects.filter(filter_params).values_list(
                'id', flat=True
            )
        return set(ids)

    def get(self, query_object, results):
        """
        Fetch records from the API. ATWS automatically makes multiple separate
        queries if the request is over 500 records.
        """
        logger.info(
            'Fetching {} records'.format(self.model_class)
        )
        batch_size = self.request_settings.get('batch_size')
        if batch_size:
            queries = self.get_batch_queries()
            for query in queries:
                for record in self.at_api_client.query(query):
                    self.persist_record(record, results)
        else:
            # Iterate over suds objects returned from the API.
            for record in query_object:
                self.persist_record(record, results)

        return results

    def persist_record(self, record, results):
        """Persist each record to the DB."""
        try:
            with transaction.atomic():
                _, created = self.update_or_create_instance(record)
            if created:
                results.created_count += 1
            else:
                results.updated_count += 1
        except InvalidObjectException as e:
            logger.warning('{}'.format(e))

        results.synced_ids.add(int(record[self.lookup_key]))

        return results

    def update_or_create_instance(self, record):
        """Creates and returns an instance if it does not already exist."""
        created = False
        api_instance = Client.dict(record)

        try:
            instance_pk = api_instance[self.lookup_key]
            instance = self.model_class.objects.get(pk=instance_pk)
        except self.model_class.DoesNotExist:
            instance = self.model_class()
            created = True

        try:
            self._assign_field_data(instance, api_instance)
            instance.save()
        except IntegrityError as e:
            msg = "IntegrityError while attempting to create {}." \
                  " Error: {}".format(self.model_class, e)
            logger.error(msg)
            raise InvalidObjectException(msg)

        logger.info(
            '{}: {} {}'.format(
                'Created' if created else 'Updated',
                self.model_class.__name__,
                instance
            )
        )

        return instance, created

    def prune_stale_records(self, initial_ids, synced_ids):
        """
        Delete records that existed when sync started but were
        not seen as we iterated through all records from the API.
        """
        stale_ids = initial_ids - synced_ids
        deleted_count = 0
        if stale_ids:
            delete_qset = self.model_class.objects.filter(pk__in=stale_ids)
            deleted_count = delete_qset.count()

            logger.info(
                'Removing {} stale records for model: {}'.format(
                    len(stale_ids), self.model_class,
                )
            )
            delete_qset.delete()

        return deleted_count

    def get_batch_queries(self):
        batch_size = self.request_settings.get('batch_size')
        finished = False
        min_id = 0
        limit_index = batch_size
        queries = []

        query = Query(self.model_class.__name__)
        query.WHERE('id', query.GreaterThanorEquals, min_id)
        xml = query.get_query_xml()

        result = self.at_api_client.client.service.query(xml)
        result_list = list(result)
        result_count = helpers.query_result_count(result)

        if result_count > batch_size:

            while not finished:
                query = Query(self.model_class.__name__)
                query.WHERE('id', query.GreaterThanorEquals, min_id)

                if limit_index < result_count:
                    try:
                        max_id = result_list[1][1][0][limit_index].id
                    except IndexError:
                        pass

                    query.AND('id', query.LessThanOrEquals, max_id)
                else:
                    finished = True

                queries.append(query)
                min_id = max_id
                limit_index += batch_size
        else:
            queries.append(query)

        return queries

    @log_sync_job
    def sync(self):
        sync_job_qset = models.SyncJob.objects.filter(
            entity_name=self.model_class.__name__
        )
        results = SyncResults()
        query = Query(self.model_class.__name__)

        if sync_job_qset.exists() and self.last_updated_field \
                and not self.full:
            last_sync_job_time = sync_job_qset.last().start_time
            query.WHERE(self.last_updated_field,
                        query.GreaterThanorEquals, last_sync_job_time)

        else:
            query.WHERE('id', query.GreaterThanorEquals, 0)

        query_object = self.at_api_client.query(query)

        # Set of IDs of all records prior
        # to sync, to find stale records for deletion.
        initial_ids = self._instance_ids()
        results = self.get(query_object, results)

        if self.full:
            results.deleted_count = self.prune_stale_records(
                initial_ids, results.synced_ids
            )

        return \
            results.created_count, results.updated_count, results.deleted_count


class PicklistSynchronizer(Synchronizer):
    lookup_key = 'Value'

    @log_sync_job
    def sync(self):
        """
        Fetch picklist for a field from the API and persist in the database.
        """
        results = SyncResults()
        picklist_objects = None
        initial_ids = self._instance_ids()

        field_info = \
            helpers.get_field_info(self.at_api_client, self.entity_type)

        try:
            field_picklist = \
                picklist.get_field_picklist(self.picklist_field, field_info)
            picklist_objects = field_picklist.PicklistValues[0]

        except KeyError as e:
            logger.warning(
                'Failed to find {} picklist field. {}'.format(
                    self.picklist_field, e
                )
            )

        if picklist_objects:
            for record in picklist_objects:
                self.persist_record(record, results)

        if self.full:
            results.deleted_count = self.prune_stale_records(
                initial_ids, results.synced_ids
            )

        return \
            results.created_count, results.updated_count, results.deleted_count

    def _assign_field_data(self, instance, object_data):

        instance.id = object_data.get('Value')
        instance.label = object_data.get('Label')
        instance.is_default_value = object_data.get('IsDefaultValue')
        instance.sort_order = object_data.get('SortOrder')
        instance.parent_value = object_data.get('ParentValue')
        instance.is_active = object_data.get('IsActive')
        instance.is_system = object_data.get('IsSystem')

        return instance


class TicketSynchronizer(Synchronizer):
    model_class = models.Ticket
    last_updated_field = 'LastActivityDate'

    related_meta = {
        'Status': (models.TicketStatus, 'status'),
        'AssignedResourceID': (models.Resource, 'assigned_resource'),
        'Priority': (models.TicketPriority, 'priority'),
        'QueueID': (models.Queue, 'queue'),
        'AccountID': (models.Account, 'account'),
        'ProjectID': (models.Project, 'project'),
        'TicketCategory': (models.TicketCategory, 'category'),
        'TicketType': (models.TicketType, 'type'),
        'Source': (models.Source, 'source'),
        'IssueType': (models.IssueType, 'issue_type'),
        'SubIssueType': (models.SubIssueType, 'sub_issue_type'),
    }

    def _assign_field_data(self, instance, object_data):
        instance.id = object_data['id']
        instance.title = object_data['Title']

        instance.ticket_number = object_data.get('TicketNumber')
        instance.completed_date = object_data.get('CompletedDate')
        instance.create_date = object_data.get('CreateDate')
        instance.description = object_data.get('Description')
        instance.due_date_time = object_data.get('DueDateTime')
        instance.estimated_hours = object_data.get('EstimatedHours')
        instance.last_activity_date = object_data.get('LastActivityDate')

        self.set_relations(instance, object_data)
        return instance


class TicketPicklistSynchronizer(PicklistSynchronizer):
    entity_type = 'Ticket'


class TicketStatusSynchronizer(TicketPicklistSynchronizer):
    model_class = models.TicketStatus
    picklist_field = 'Status'


class TicketPrioritySynchronizer(TicketPicklistSynchronizer):
    model_class = models.TicketPriority
    picklist_field = 'Priority'


class QueueSynchronizer(TicketPicklistSynchronizer):
    model_class = models.Queue
    picklist_field = 'QueueID'


class TicketCategorySynchronizer(TicketPicklistSynchronizer):
    model_class = models.TicketCategory
    picklist_field = 'TicketCategory'


class SourceSynchronizer(TicketPicklistSynchronizer):
    model_class = models.Source
    picklist_field = 'Source'


class IssueTypeSynchronizer(TicketPicklistSynchronizer):
    model_class = models.IssueType
    picklist_field = 'IssueType'


class SubIssueTypeSynchronizer(TicketPicklistSynchronizer):
    model_class = models.SubIssueType
    picklist_field = 'SubIssueType'


class TicketTypeSynchronizer(TicketPicklistSynchronizer):
    model_class = models.TicketType
    picklist_field = 'TicketType'


class ProjectStatusSynchronizer(PicklistSynchronizer):
    model_class = models.ProjectStatus
    entity_type = 'Project'
    picklist_field = 'Status'


class ProjectTypeSynchronizer(PicklistSynchronizer):
    model_class = models.ProjectType
    entity_type = 'Project'
    picklist_field = 'Type'


class ResourceSynchronizer(Synchronizer):
    model_class = models.Resource
    last_updated_field = None

    def _assign_field_data(self, instance, object_data):
        instance.id = object_data['id']
        instance.user_name = object_data.get('UserName')
        instance.email = object_data.get('Email')
        instance.first_name = object_data.get('FirstName')
        instance.last_name = object_data.get('LastName')
        instance.active = object_data.get('Active')

        return instance


class TicketSecondaryResourceSynchronizer(Synchronizer):
    model_class = models.TicketSecondaryResource
    last_updated_field = None

    related_meta = {
        'ResourceID': (models.Resource, 'resource'),
        'TicketID': (models.Ticket, 'ticket'),
    }

    def _assign_field_data(self, instance, object_data):
        instance.id = object_data['id']
        self.set_relations(instance, object_data)

        return instance


class AccountSynchronizer(Synchronizer):
    model_class = models.Account
    last_updated_field = 'LastActivityDate'

    def _assign_field_data(self, instance, object_data):
        instance.id = object_data['id']
        instance.name = object_data.get('AccountName')
        instance.number = object_data.get('AccountNumber')
        instance.active = object_data.get('Active')
        instance.last_activity_date = object_data.get('LastActivityDate')

        return instance


class ProjectSynchronizer(Synchronizer):
    model_class = models.Project
    last_updated_field = 'LastActivityDateTime'

    related_meta = {
        'ProjectLeadResourceID': (models.Resource, 'project_lead_resource'),
        'AccountID': (models.Account, 'account'),
        'Status': (models.ProjectStatus, 'status'),
        'Type': (models.ProjectType, 'type'),
    }

    def _assign_field_data(self, instance, object_data):

        completed_date = object_data.get('CompletedDateTime')
        end_date = object_data.get('EndDateTime')
        start_date = object_data.get('StartDateTime')

        instance.id = object_data['id']
        instance.name = object_data.get('ProjectName')
        instance.number = object_data.get('ProjectNumber')
        instance.description = object_data.get('Description')
        instance.actual_hours = object_data.get('ActualHours')
        instance.completed_percentage = object_data.get('CompletedPercentage')
        instance.duration = object_data.get('Duration')
        instance.estimated_time = object_data.get('EstimatedTime')
        instance.last_activity_date_time = \
            object_data.get('LastActivityDateTime')

        if completed_date:
            instance.completed_date = completed_date.date()

        if end_date:
            instance.end_date = end_date.date()

        if start_date:
            instance.start_date = start_date.date()

        self.set_relations(instance, object_data)

        return instance
