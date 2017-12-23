import json
from tornado.httpclient import AsyncHTTPClient
from urllib.parse import quote_plus
from scope import Scope

class LUIS:  # handles interaction with LUIS framework

    # --- CLASS METHODS ---
    @classmethod
    def pluralize(cls, word, check_amount):  # returns the word in plural form if the 'check_amount' > 1
        assert type(word) is str and type(check_amount) is int
        if (check_amount > 1) and (word[-1] != "s"): return word + "s"
        return word  # default is to return word back unchanged

    @classmethod
    def joinWithAnd(cls, words, joiner="and", prefix=False):  # return <Str> w/ the words in list separated by commas
        assert type(words) is list
        string = ""
        for i, word in enumerate(words):
            if (i == len(words) - 1) and (i != 0):  # LAST word in list (and NOT the FIRST word)
                if len(words) != 2: string += ","  # use oxford comma for 3+ word list
                string += " {} ".format(joiner)  # separate w/ the joining word
            elif i != 0: string += ", "  # NOT the last OR first word - separate w/ comma
            if prefix:  # check if we should add a prefix (a or an)
                if word[-1] == "s":  # PLURAL word - no prefix
                    pass
                elif word[0].lower() in ["a", "e", "i", "o", "u"]:  # vowel start - prefix w/ "AN"
                    string += "an "
                else:  # consonant start - prefix with "A"
                    string += "a "
            string += word.strip()
        return string

    # --- INSTANCE METHODS ---
    def __init__(self, query, activity):  # intialize w/ query made by user & activity for the query
        print("\nInitializing LUIS request w/ query=['{}']".format(query))
        luis_enpoint_url = "https://westus.api.cognitive.microsoft.com/luis/v2.0/apps/" \
                           "1cd07ebb-3a30-4662-a655-bed6d8805aa4?subscription-key=3c42debb60f546b2bf166876a0f1ab0c&" \
                           "timezoneOffset=0&spellCheck=true&verbose=true&q="
        self.__url = luis_enpoint_url + "{}".format(quote_plus(query))  # append URL FORMATTED query -> final URL
        self.__query = query  # cache the query (*need it to be logged to some data store*)
        self.__activity = activity  # cache the activity
        self.__db_handler = activity.getDatabaseHandler()  # cache the db handler
        self.__topIntent = None  # <Intent> with highest probability for query
        self.__intents = []  # <list[Intent]> lists each intent w/ the assigned probability for the query
        self.__entities = []  # <list[Entities]> lists each entity that was found in the query
        self.__scope = None  # handles all scope logic while processing a request
        self.__response = None  # response that is passed back -> the user
        self.__is_clarification = False  # indicates whether current Intent is for clarification
        self.passQueryToApp()  # generate the async request -> our LUIS app

    # def modifyPronoun(self, verb):  # modifies pronoun/verb pair based on age & gender
    #     pronoun = "I"  # initialize w/ 1st person pronoun (default)
    #     value, unit = self.__patient.age
    #     if (unit in ["day", "week", "month"]) or (unit == "year" and value <= 10):  # Pediatric cases
    #         if self.__patient.gender == "male":  # male patient
    #             pronoun = "he"
    #         else:  # female patient
    #             pronoun = "she"
    #
    #     # (2) Modify verb based on pronoun:
    #     if (verb == "am") and (pronoun != "I"):
    #         verb = "is"
    #     elif (verb == "have") and (pronoun != "I"):
    #         verb = "has"
    #     elif (verb == "do") and (pronoun != "I"):
    #         verb = "does"
    #     elif verb == "had":
    #         pass  # no modification for pronoun needed
    #     elif pronoun != "I":  # catchall for other verbs
    #         verb += "s"  # add s to end (e.g. I take vs. he takes)
    #     return (pronoun, verb)  # return TUPLE w/ (pronoun, verb)

    def nextWordAfter(self, entity_value, matches, entity_type=None):  # checks word AFTER entity in query for matches
        print("\nDoes word AFTER [{}] match {}?".format(entity_value, matches))
        assert type(matches) is list
        entities = self.findMatchingEntity(of_type=entity_type, value=[entity_value], full=True)  # find entity match
        if len(entities) > 0:  # make sure entities are found
            for e in entities:  # perform check for EACH matching entity
                end_index = e.endIndex + 1  # access 1 character PAST the end index of the FIRST match
                sub_string = self.__query[end_index:]  # get query sentence PAST point of provided index
                next_word = ""  # initialize return object
                for c in sub_string:  # iterate character by character through remainder of query
                    if c.isalpha():
                        next_word += c  # build word char-by-char
                    else:  # non-alpha character
                        if len(next_word) > 0:  # word has already been created
                            if next_word in matches:  # check for match in input list
                                print("[{}] is a match!".format(next_word))
                                return True  # match found
                            else:  # NOT a match
                                break  # break INNER loop
        return False  # default - indicates no match

    def passQueryToApp(self):  # send query -> LUIS application
        client = AsyncHTTPClient()  # create an asynchronous request
        client.fetch(self.__url, self.handle_response)  # defines callball to handle the response

    def handle_response(self, response):  # CALLBACK method for the asynchronous web request
        print("\n[callback] Received response from LUIS app:")
        if response.error:  # check for error from LUIS service
            print("[LUIS Error] {}".format(response.error))
            self.__db_handler.logQueryData(self.__activity.getConversationID(), self.__query)  # log query
            self.__db_handler.logError(self.__activity.getConversationID(), "LUIS_ERROR: {}".format(response.error))
            self.__activity.sendTextMessage(text="Sorry, I didn't understand that. "
                                                 "Please try rephrasing your message.")  # send error msg
        else:  # successful web request - get intents & entities for user input
            json_data = json.loads(response.body.decode('utf-8'))  # get JSON dict from HTTP body
            altered_query = json_data.get('alteredQuery', None)  # if spell check alters the query
            self.__topIntent = Intent(json_data.get('topScoringIntent', None))  # access the HIGHEST probability intent
            self.__intents = [Intent(i) for i in json_data.get('intents', list())]  # access each intent & wrap in class
            self.__entities = [Entity(e) for e in json_data.get('entities', list())]  # access each entity & wrap
            print("Top Intent: ", self.__topIntent.intent)
            print(json_data.get('intents', list()))
            print(json_data.get('entities', list()))
            self.__db_handler.logQueryData(self.__activity.getConversationID(), self.__query,
                                           altered_query=altered_query,
                                           intents=self.__intents, entities=self.__entities)  # log query/LUIS response
            if altered_query:  # query was altered
                self.__query = altered_query  # *overwrite self.query AFTER logging data -> DB!*
            try:  # wrap in try statement so we can still remove blocker after server error
                self.renderResponseForQuery()
            except Exception as e:  # remove blocker on failure
                print("[{}] Unable to render response: <{}>".format(type(e), e))
                self.__db_handler.logError(self.__activity.getConversationID(), "{}: {}".format(type(e), e))
                self.__activity.sendTextMessage(text="Sorry, I didn't understand that. "
                                                     "Please try rephrasing your message.")  # send error msg

    def renderResponseForQuery(self):  # constructs a response based on the user's query intent
        self.__response = "Sorry, I didn't understand that" # (1) initialize default response message
        self.__scope = Scope(self.__db_handler.checkCurrentScope(self.__activity.getConversationID()))  # (2) init scope

        # (3) Check for a CLARIFICATION object:
        clarification = self.__db_handler.getCacheForClarification(self.__activity.getConversationID())
        if clarification is not None:  # clarification exists!
            entity_type = clarification[2]  # check what entity type we SHOULD be getting
            if len(self.findMatchingEntity(of_type=entity_type)) > 0:  # check that we received at least 1 such entity
                self.__topIntent = Intent(clarification[0])  # recreate old top scoring intent & overwrite
                updated_e = [Entity(e) for e in clarification[1]]  # create old entities
                for e in self.__entities:  # add the NEW entities to the END of the existing list (*to preserve order!*)
                    updated_e.append(e)
                self.__entities = updated_e  # overwrite the entities object
                self.__is_clarification = True  # set indicator (needed for findObject logic)
            # *If no entities of specified type are found, simply treat query normally & ignore clarification!*

        e = self.findMatchingEntity("query")  # *(4) check for a QUERY entity AFTER the clarification!*
        query_word = e[0] if len(e) > 0 else ""  # store FIRST query that is found (b/c entities are sent IN ORDER)

        # Non-Historical Intents:
        if self.__topIntent.intent == "None":  # receptacle for unwanted questions ***
            pass
        elif self.__topIntent.intent == "Greeting":
            name = self.__activity.getUserName()  # check if user's name is defined
            self.__response = "Hello, {}".format(name[1]) if name else "Hello"
        elif self.__topIntent.intent == "GetName":  # asking for name
            self.__response = "I am the Diagnostic Bot"

        # Recognizer Intents:


        # (LAST) Persist the scope object & then render the bot's response:
        self.__db_handler.persistCurrentScope(self.__activity.getConversationID(), self.__scope.getScopeForDB())
        self.__activity.sendTextMessage(text="{}".format(self.__response))  # send msg

    # --- HELPER FUNCTIONS ---
    def findMatchingEntity(self, of_type=None, value=list(), full=False):  # looks for entity based on TYPE &/or VALUE
        # 'full': FALSE -> returns ONLY entity NAME, TRUE -> returns FULL entity object
        assert type(value) is list  # make sure value is INPUT as a LIST of STRINGs
        matches = list()  # initialize return object
        for e in self.__entities:  # loop through list of entities
            if e.isEntity(of_type, value):
                if full:  # return entity OBJECT
                    matches.append(e)
                else:  # return entity NAME
                    matches.append(e.entity)  # check for matches & add -> return object
        return matches  # return all matches as a LIST


class Intent:  # 'intent': represents a task or action a user wants to perform, expressed by the user's input
    # Intents match user inputs w/ actions that should be taken by your app | LIMIT 80
    # "None" intent: intents which are IRrelevant to your app
    # Utterances: sentences representing examples of the kinds of user queries your app is expecting
    # LUIS learns from the sample utterances & can generalize & understand similar contexts
    # Each utterance is labeled by (i.e. categorized as) an INTENT
    def __init__(self, json_dict):  # initialize w/ JSON from LUIS response
        self.intent = json_dict.get('intent', None)  # <string>
        self.score = json_dict.get('score', None)  # <float>

class Entity:  # an entity represents a collection of similar objects (e.g. places, things, people, concepts)
    # Entities are words/phrases that have been labeled in the defined UTTERANCES
    # 3 types of entities - (1) PRE-BUILT: e.g. dates/numbers, automatically labeled by LUIS | NO LIMIT
    # (2) CUSTOM: simple (single concept), hierarchical (parent & children), composite (compound of 2+ Es) | LIMIT 30
    # (3) LIST: customized list of entity values to be used as keywords to recognize entity in utterance | LIMIT 50
    def __init__(self, json_dict):  # initialize w/ JSON from LUIS response
        self.entity = json_dict.get('entity', None)  # <string> the WORD that was classified as an entity
        self.type = json_dict.get('type', None)  # <string> the ENTITY that the word was classified to
        self.startIndex = json_dict.get('startIndex', None) # <int> startIndex of word in query
        self.endIndex = json_dict.get('endIndex', None)  # <int> endIndex of word in query
        self.score = json_dict.get('score', None)  # <float> probability of word belonging to selected entity

    def isEntity(self, of_type=None, value=list()):  # checks if entity matches search criteria
        assert type(value) is list  # make sure values are entered as a list of possibilities
        if of_type is None:  # *FIRST check if TYPE is provided!* - no type => check ONLY for entity VALUE match
            if len(value) == 0: return True  # return True for ALL entities if no type OR values are given
            elif self.entity in value: return True  # VALUE match
        elif (of_type == "builtin.geography") and (len(self.type) >= 17):  # special built-in 'geography' type
            if self.type[:17] == of_type:  # 'geography' type entity
                if len(value) == 0: return True  # no values provided - return match on type alone
                elif (self.entity in value): return True  # AT LEAST 1 value provided & entity is in provided list
        elif self.type == of_type:  # entity TYPE matches the input type
            if len(value) == 0: return True  # no values provided - return match on type alone
            elif self.entity in value: return True # AT LEAST 1 value provided & entity is in provided list
        return False  # default is false