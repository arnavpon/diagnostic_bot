import requests
import jwt
import json
from jwt.algorithms import RSAAlgorithm
from datetime import datetime, timedelta

class Authentication:  # handles authentication of incoming & outgoing messages

    # --- INSTANCE METHODS
    def __init__(self):
        self.__microsoft_app_name = "Standardized Patient Bot"  # bot name
        self.__microsoft_app_id = "b385ba32-44b3-467e-a578-8f642e0e3cb3"  # app ID generated during registration
        self.__microsoft_app_password = "2Qut0CR8smLibDMTraNxULn"  # password generated during registration

        self.__active_token = None  # init the server's active Authorization token (1 for the entire server!)
        self.__token_timeout = None  # keep track of the time @ which the token expires
        self.__jwk = None  # init the list of JSON web keys for JWT authentication
        self.__jwks_by_endorsement = None  # dict that stores a list of JWK indexes against endorsement name
        self.__secret_expiration = None  # keep track of secret key expiration date
        self.__signing_algorithm = None  # algorithm used to hash JWT signature

    def authenticateIncomingMessage(self, auth_header, service_url, channel_id):  # authenticate INCOMING message
        print("\nAuthenticating INCOMING request...".format(auth_header))
        if self.__jwk:  # FIRST check if JSON web keys exist
            if datetime.now() >= self.__secret_expiration:  # check if keys have EXPIRED
                self.getSecretKeys()  # obtain new keys
        else:  # keys do NOT exist
            self.getSecretKeys()  # obtain new keys

        # THEN use the JWT Framework to verify authenticity of the request:
        if auth_header is None: return 403  # make sure authentication header exists
        if auth_header[:6] != "Bearer": return 403 # (1) check that token was sent in Auth header w/ "Bearer" scheme

        token = auth_header[7:]  # strip the "Bearer" & access the token
        try:  # parse the JWT (using the JWK as the secret) to obtain the contained JSON data
            if (channel_id != "emulator") and (channel_id not in self.__jwks_by_endorsement):  # CONNECTOR logic
                # make sure JWKs exist for the input BotConnector channel
                print("Error - no JWKs found for endorsement '{}'!".format(channel_id))
                return 403
            key_index = 0 if (channel_id is None or channel_id == "emulator") \
                else self.__jwks_by_endorsement[channel_id][0]  # get JWK for channel
            secret = RSAAlgorithm.from_jwk(json.dumps(self.__jwk[key_index]))  # create secret by picking JWK from list
            connector_iss = "https://api.botframework.com"  # CONNECTOR issuer
            #emulator_iss = self.__jwk[key_index]['issuer']  # *** EMULATOR only - get issuer | shouldn't work but does
            # emulator_iss = "https://sts.windows.net/f8cdef31-a31e-4b4a-93e4-5f571e91255a/"  # emulator v3.2
            # emulator_iss = "https://sts.windows.net/d6d49420-f39b-4df7-a1dc-d59a935871db/"  # *** EMULATOR v3.1
            token = jwt.decode(token, secret,
                               algorithms=self.__signing_algorithm,
                               audience=self.__microsoft_app_id,
                               issuer=connector_iss)  # (6) decodes token & VERIFIES JWT signature/audience/issuer
        except jwt.ImmatureSignatureError:  # signature is NOT YET valid
            print("JWT is still immature, passing through jwt.decode again...")
            return 000  # special internal code to indicate immature JWT
        except jwt.InvalidIssuerError:  # (3) validate that the ISSUER is valid (handled by jwt automatically)
            print("Error - the JWT ISSUER is invalid!")
            return 403
        except jwt.InvalidAudienceError:  # (4) validate that AUDIENCE matches the App ID (handled by jwt)
            print("Error - the JWT AUDIENCE does not match the App ID!")
            return 403
        except jwt.ExpiredSignatureError:  # (5) make sure token hasn't expired yet (handled by jwt)
            print("Error - the JWT has EXPIRED!")
            return 403
        except Exception as e:  # uncaught exception
            print("[{}] Error decoding JWT - '{}'".format(type(e).__name__, e.args))
            return 403
        else:  # (7) check the token's service URL (must match the Activity serviceUrl)
            token_url = token.get("serviceUrl", None) # CONNECTOR only
            if (token_url is not None) and (token_url != service_url):
                print("Error - Activity serviceURL [{}] does NOT match tokenURL [{}]".format(service_url, token_url))
                return 403
            #app_id = token.get("appid", None)  # *** EMULATOR only - after update, 'appid' key is no longer in token!
            #app_id = token.get("azp", None)  # EMULATOR only - AFTER update, access the 'azp' property
            #if app_id != self.__microsoft_app_id:
            #    print("Error - appID {} does not match microsoft appID!".format(app_id))
            #    return 403  # *** EMULATOR only
        return 200  # if all checks are passed, return 200 OK status

    def getSecretKeys(self):  # obtains secret keys from Microsoft's authentication server
        print("Obtaining new JWKs from authentication server...")
        emulator_url = "https://login.microsoftonline.com/botframework.com/v2.0/.well-known/openid-configuration"
        connector_url = "https://login.botframework.com/v1/.well-known/openidconfiguration"
        request_1 = requests.get(connector_url)  # (1) get openID document
        request_body = request_1.json()
        self.__signing_algorithm = request_body['id_token_signing_alg_values_supported']
        jwk_uri = request_body['jwks_uri']  # (2) access URI that specifies location of Bot service's signing keys

        request_2 = requests.get(jwk_uri)  # send request -> JWK URI
        self.__jwk = request_2.json()['keys']  # (3) obtain signing KEYS from response & cache for 5 days
        self.__secret_expiration = datetime.now() + timedelta(days=5)  # set the expiration date for 5 days from now
        temp = dict()  # initialize temporary dict w/ KEY = endorsement name, VALUE = index of jwks for endorsement
        for i, key in enumerate(self.__jwk):  # INDEX each key by its endorsements
            ENDORSEMENT_KEY = "endorsements"
            if ENDORSEMENT_KEY in key:  # safety check (for emulator compatibility)
                endorsements = key[ENDORSEMENT_KEY]  # list of endorsements for JWK
                for e in endorsements:
                    if e not in temp:
                        temp[e] = list()  # initialize
                    temp[e].append(i)  # store key's index in array to enable lookup @ authentication time
        self.__jwks_by_endorsement = temp  # store index -> self property
        print("Secret keys expire 5d from now on [{}]".format(self.__secret_expiration))

    def authenticateOutgoingMessage(self):  # authenticate the OUTGOING message to the user client
        if self.__active_token:  # active token EXISTS - check that token is NOT expired
            if datetime.now() < self.__token_timeout:  # current time is LESS than timeout time (ACTIVE token)
                return self.__active_token  # return cached token
        return self.getAuthorizationToken()  # no active token - request & return a NEW one

    def getAuthorizationToken(self):  # requests Microsoft for a new Authorization token
        url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
        request_data = "grant_type=client_credentials&" \
                       "client_id={}&" \
                       "client_secret={}&" \
                       "scope=https%3A%2F%2Fapi.botframework.com%2F.default".format(self.__microsoft_app_id,
                                                                                    self.__microsoft_app_password)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "login.microsoftonline.com"
        }
        request = requests.post(url, data=request_data.encode(), headers=headers)  # send HTTP request
        auth_dict = request.json()  # access JSON
        token = auth_dict['access_token']  # get token from dict
        self.__active_token = token  # *CACHE the active token*

        # Cache the token timeout:
        timeout = auth_dict['expires_in']  # get lifespan of token (usu. 3600 seconds = 1 hour)
        td = timedelta(seconds=timeout)  # define time delta using timeout
        self.__token_timeout = datetime.now() + td  # cache the expiration time
        print("Obtained NEW Auth Token: [{}]\nToken expires @ {}".format(token, self.__token_timeout))
        return token