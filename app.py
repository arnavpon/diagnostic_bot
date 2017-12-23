import os
import json
import activity
import time
import requests
from tornado import ioloop, web
from datetime import datetime
from database import DatabaseHandler
from authentication import Authentication

ip = os.environ.get("SP_BOT_SERVICE_HOST", None)  # access OpenShift environment host IP
host_port = os.environ.get("SP_BOT_SERVICE_PORT", 8000)  # access OpenShift environment PORT
db_handler = DatabaseHandler()  # initialize database handler object
authenticator = Authentication()  # initialize authentication object

class MainHandler(web.RequestHandler):

    # --- REQUEST HANDLERS ---
    def get(self, *args, **kwargs):  # incoming GET request
        print("\nParsing GET request...")
        self.write("Hello, world from Diagnostic Bot!")

    def post(self, *args, **kwargs):  # incoming POST request
        print("\n[{}] Received POST Request from client...".format(datetime.now()))

        # (1) Decode the POST data -> a dictionary:
        json_data = self.request.body.decode('utf-8')  # obtain POST body from request, decode from bytes -> Str
        post_body = json.loads(json_data)  # convert JSON data -> dict

        # (2) Authenticate incoming message & generate a response header:
        auth_header = self.request.headers.get('Authorization', None)
        service_url = post_body.get("serviceUrl", None)
        channel_id = post_body.get("channelId", None)
        psid = post_body['from'].get('id', None) if 'from' in post_body else None
        if psid is not None:  # turn on the sender action
            self.turnOnSenderAction(channel_id, psid)
        status = authenticator.authenticateIncomingMessage(auth_header, service_url, channel_id)  # authenticate req
        while status == 000:  # immature token
            time.sleep(0.05)  # brief delay before attempting to decode token again
            status = authenticator.authenticateIncomingMessage(auth_header, service_url, channel_id)
        self.set_header("Content-type", "application/json")
        if status != 200:  # authentication was UNSUCCESSFUL - terminate function
            print("Authorization failed")
            self.set_status(status, "Access Denied")  # return status code
            return  # terminate function here!

        # (3) If the request was successfully authenticated, init an <Activity> object & provide flow control:
        conversation = post_body['conversation']['id']  # cache the conversationID (identifies each UNIQUE user)
        print("\nConversation ID = {}".format(conversation))
        position = db_handler.getPositionInFlow(conversation)  # check current position in flow
        print("Current position in conversation = [{}]".format(position))

        user = db_handler.getUsername(conversation)  # get user to pass -> Activity
        current_activity = activity.Activity(db_handler, authenticator, post_body, position, user)  # init activity
        db_handler.updateConversation(conversation, activity.UPDATED_POSITION, current_activity.getUserName())

        # if (patient) and (post_body.get("text", None) is not None):  # patient exists AND incoming msg is TEXT
        #     print("Blocker Set? {}".format(patient.isBlocked(conversation)))
        #     if not patient.isBlocked(conversation):  # blocker is NOT set - pass activity through
        #         patient.setBlock(conversation)  # set blocker BEFORE initializing the new activity
        #         current_activity = activity.Activity(authenticator, post_body, position, user, patient)  # init
        #         db_handler.updateConversation(conversation, activity.UPDATED_POSITION, current_activity.getUserName())
        # else:  # initialization flow
        #     current_activity = activity.Activity(authenticator, post_body, position, user, patient)  # init Activity
        #     db_handler.updateConversation(conversation, activity.UPDATED_POSITION, current_activity.getUserName())

    # --- INSTANCE METHODS ---
    def turnOnSenderAction(self, channel, psid):  # Facebook - turns on sender action (... typing on chat)
        if channel == "facebook":  # make sure this is Facebook channel
            access_token = "EAAD7sZBOYsK4BAJt95X17v6ZCstfHi3UgUkJZCcetgVEJpH6tFN5Ju3zQ2CTXJ" \
                            "M35o8gteO17Ixk5N96gQxUIJug5IsjSozCEogiuqgKQEfWGMf9HlIABFyC7wC4cRkugwaLssad" \
                            "9AVuPFXkw6muELn9jljXmL964bqvZCvioQZDZD"
            url = "https://graph.facebook.com/v2.6/me/messages?access_token={}".format(access_token)
            data = {
                "recipient": {
                    "id": psid
                },
                "sender_action": "typing_on"
            }
            requests.post(url, json=data, headers={"Content-Type": "application/json"})  # post action -> Facebook

if __name__ == '__main__':
    print("[{}] Starting HTTP server @ IP {} & Port {}...".format(datetime.now(), ip, host_port))
    static_dir = os.path.join(os.path.abspath('.'), 'static')  # get path -> static directory
    app = web.Application([
        (r"/", MainHandler),
        (r"/static/(.*)", web.StaticFileHandler, {"path": static_dir, "default_filename": "privacy_policy.html"})
    ])  # routes requests to the root url '/' -> the MainHandler class, requests -> '/static/' to a file handler
    app.listen(host_port)  # listen @ localhost port (default is 8000 unless specified in os.environ variable)
    ioloop.IOLoop.instance().start()  # start the main event loop
