
from superdesk import Resource, Service
from superdesk.utils import ListCursor
from superdesk.errors import AlreadyExistsError


registered_search_providers = {}
allowed_search_providers = []


def register_search_provider(name, fetch_endpoint=None, provider_class=None):
    """Register a Search Provider with the given name and fetch_endpoint.

    Both have to be unique and if not raises AlreadyExistsError.
    The fetch_endpoint is used by clients to fetch the article from the Search Provider.

    :param name: Search Provider Name
    :type name: str
    :param fetch_endpoint: relative url to /api
    :type fetch_endpoint: str
    :param provider_class: provider implementation
    :type provider: superdesk.SearchProvider
    :raises: AlreadyExistsError - if a search has been registered with either name or fetch_endpoint.
    """
    if name in registered_search_providers:
        raise AlreadyExistsError("A Search Provider with name: {} already exists".format(name))

    if fetch_endpoint and fetch_endpoint in registered_search_providers.values():
        raise AlreadyExistsError("A Search Provider for the fetch endpoint: {} exists with name: {}"
                                 .format(fetch_endpoint, registered_search_providers[name]))

    registered_search_providers[name] = provider_class if provider_class else fetch_endpoint

    if not registered_search_providers[name]:
        raise ValueError('You have to specify fetch_endpoint or provider_class.')

    allowed_search_providers.append(name)


class SearchProviderAllowedResource(Resource):
    resource_methods = ['GET']
    item_methods = []


class SearchProviderAllowedService(Service):

    def get(self, req, lookup):
        def provider(provider_id):
            registered = registered_search_providers[provider_id]
            return {
                'search_provider': provider_id,
                'label': getattr(registered, 'label', provider_id)
            }

        return ListCursor(
            [provider(_id) for _id in registered_search_providers]
        )
