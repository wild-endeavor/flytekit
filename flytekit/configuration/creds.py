from __future__ import absolute_import

from flytekit.configuration import common as _config_common

DISCOVERY_ENDPOINT = _config_common.FlyteStringConfigurationEntry('credentials', 'discovery_endpoint', default='https://company.idp.com/.well-known/oauth-authorization-server')
"""
This endpoint fetches authorization server metadata as described in:
https://tools.ietf.org/html/rfc8414
The endpoint path can be relative or absolute.
"""

CLIENT_ID = _config_common.FlyteStringConfigurationEntry('credentials', 'client_id', default=None)
"""
This is the public identifier for the app which handles authorization for a Flyte deployment.
More details here: https://www.oauth.com/oauth2-servers/client-registration/client-id-secret/.
"""

REDIRECT_URI = _config_common.FlyteStringConfigurationEntry('credentials', 'redirect_uri', default="http://localhost:53593/callback")
"""
This is the callback uri registered with the app which handles authorization for a Flyte deployment.
Please note the hardcoded port number. Ideally we would not do this, but some IDPs do not allow wildcards for
the URL, which means we have to use the same port every time. This is the only reason this is a configuration option,
otherwise, we'd just hardcode the callback path as a constant.
FYI, to see if a given port is already in use, run `sudo lsof -i :<port>` if on a Linux system.
More details here: https://www.oauth.com/oauth2-servers/redirect-uris/.
"""

AUTHORIZATION_METADATA_KEY = _config_common.FlyteStringConfigurationEntry('credentials', 'authorization_metadata_key',
                                                                          default="authorization")
"""
The authorization metadata key used for passing access tokens in gRPC requests.
Traditionally this value is 'authorization' however it is made configurable.
"""

# TODO(katrogan) Make this an enum rather than a string config entry
AUTH_MODE = _config_common.FlyteStringConfigurationEntry('credentials', 'auth_mode', default="standard")
"""
The auth mode defines the behavior used to request and refresh credentials. The currently supported modes include:
- 'standard' This uses the pkce-enhanced authorization code flow by opening a browser window to initiate credentials
        access.
- 'basic' This uses cert-based auth in which the end user enters his/her username and password and public key encryption
        is used to facilitate authentication.
"""