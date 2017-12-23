from pymongo import MongoClient
from copy import deepcopy
from datetime import datetime, timedelta

class DatabaseHandler:

    def __init__(self):
        client = MongoClient("mongodb://localhost:27017/")
        self.__db = client.diagnostics  # specify the 'diagnostics' DB for this project

    def initializeConversationRecord(self, conversation):  # creates conversation record in DB if it doesn't exist
        record = self.__db.conversations.find_one({"conversation": conversation})  # check if conversation is already in DB
        if record is None:  # conversation does NOT already exist
            self.__db.conversations.insert_one({"conversation": conversation,
                                                "position": 0})  # insert record

    def updateConversation(self, conversation, position, user):  # updates the position/user/timestamp for conversation
        self.initializeConversationRecord(conversation)
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$set': {"position": position,
                      "user": user,
                      "timestamp": datetime.now()}}
        )

    def setBlock(self, conversation):  # sets blocker to prevent new Activity from being created
        print("\n[Patient] SETTING blocker...")
        self.initializeConversationRecord(conversation)
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$set': {"isBlocked": True}}
        )

    def removeBlock(self, activity):  # removes blocker to allow Activity to be created
        print("\n[Patient] REMOVING blocker for {}...".format(activity.getConversationID()))
        activity.turnOffSenderAction()  # turns off the typing (...) indicator in chat

        conversation = activity.getConversationID()
        self.initializeConversationRecord(conversation)
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {"$set": {"isBlocked": False}}
        )  # remove blocker

    def isBlocked(self, conversation):  # checks if blocker is set for the given conversation
        record = self.__db.conversations.find_one({"conversation": conversation})  # check if conversation is in DB
        if record:  # conversation ALREADY exists
            return record.get("isBlocked", False)  # return the current blocker value or False if key is missing
        return False  # default return value if record is not found

    def persistCurrentScope(self, conversation, scope):  # called by LUIS class, persists the open scope
        self.initializeConversationRecord(conversation)
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$set': {"scope": scope}}
        )  # store the scope

    def checkCurrentScope(self, conversation):  # searches for the open scope for a given conversation
        record = self.__db.conversations.find_one({"conversation": conversation}, projection={"scope": 1})
        return record.get("scope", None) if record else None  # pass back the scope if it is found

    def removeScope(self, record):  # removes the existing scope from the DB record
        self.__db.conversations.update_one(record, {"$unset": {"scope": None}})  # remove the 'scope' value
        print("Closed scope for conversation [{}]...".format(record["conversation"]))

    def cacheQueryForClarification(self, conversation, top_intent, entities, e_type):  # clarification CACHING logic
        # e_type: specified entity type we SHOULD be receiving with the next request to perform clarification logic
        # Store a COMPLETE representation of the topIntent + all entities:
        self.initializeConversationRecord(conversation)
        intent = {"intent": top_intent.intent, "score": top_intent.score}
        entities = [{"entity": e.entity, "type": e.type,
                     "startIndex": e.startIndex, "endIndex": e.endIndex, "score": e.score} for e in entities]
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$set': {"clarification": [intent, entities, e_type]}}
        )  # add clarification info

    def getCacheForClarification(self, conversation):  # clarification FETCH logic
        record = self.__db.conversations.find_one({"conversation": conversation})  # check if conversation is in DB
        if record:  # conversation ALREADY exists
            data = deepcopy(record.get("clarification", None))  # get a copy of the data
            self.__db.conversations.update_one(record, {"$unset": {"clarification": None}})  # *REMOVE clarification!*
            return data  # pass back the topScoring intent + all entities (2 element list)
        return None  # default - None => no clarification

    # --- LOGGING BEHAVIOR ---
    def logName(self, conversation, username):  # logs the name of the user having the conversation
        # the name is used to email specific users asking for feedback about their experiences
        if username:  # only log names that are NOT None
            self.initializeConversationRecord(conversation)
            self.__db.conversations.update_one(
                {'conversation': conversation},
                {'$set': {"user": username}}
            )  # add the USER field

    def logError(self, conversation, error):  # stores any errors
        self.initializeConversationRecord(conversation)
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$push': {'queries': "[ERROR] {}".format(error)}}
        )  # add error as its own entry in the array

    def logQueryData(self, conversation, query, altered_query="", intents=list(), entities=list()):
        # logs each user query/LUIS classification as tuple: (query, [(intent, probability)], [entities])
        self.initializeConversationRecord(conversation)
        limit = 3  # number of intents to store
        if len(intents) <= limit:  # store ALL intents if less than threshold
            top_intents = intents[:]
        else:  # more intents than limit
            top_intents = intents[:limit]  # store only the limit (in order from greatest to lowest probability)
        entry = ((query, altered_query), [(i.intent, i.score) for i in top_intents],
                 [(e.entity, e.type, e.startIndex, e.endIndex) for e in entities])
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$push': {'queries': entry}}
        )  # add each complete query record as its own entry in the array

    def logResponse(self, conversation, text_response, status_code, reason):  # logs the response sent by bot -> user
        self.initializeConversationRecord(conversation)
        response = {
            "message": text_response,
            "http_response": "{}: {}".format(status_code, reason),
        }
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$push': {'queries': response}}
        )

    def logFeedback(self, conversation, user_input):  # stores user feedback for the converation
        self.initializeConversationRecord(conversation)
        self.__db.conversations.update_one(
            {'conversation': conversation},
            {'$push': {'feedback': user_input}}
        )

    # --- GETTERS ---
    def getPositionInFlow(self, conversation):  # returns current position for conversation
        record = self.__db.conversations.find_one({"conversation": conversation})  # check if conversation is in DB
        if record:  # conversation ALREADY exists
            return record.get("position", 0)  # pass back the position
        return 0  # default -> 0

    def getUsername(self, conversation):  # returns the Username if it exists
        record = self.__db.conversations.find_one({"conversation": conversation})  # check if conversation is in DB
        if record:  # conversation ALREADY exists
            return record.get("user", None)  # pass back the username
        return None