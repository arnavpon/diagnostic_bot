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
        self.__patient = activity.getPatient()  # cache the patient
        self.__topIntent = None  # <Intent> with highest probability for query
        self.__intents = []  # <list[Intent]> lists each intent w/ the assigned probability for the query
        self.__entities = []  # <list[Entities]> lists each entity that was found in the query
        self.__scope = None  # handles all scope logic while processing a request
        self.__response = None  # response that is passed back -> the user
        self.__is_clarification = False  # indicates whether current Intent is for clarification
        self.passQueryToApp()  # generate the async request -> our LUIS app

    def modifyPronoun(self, verb):  # modifies pronoun/verb pair based on age & gender
        pronoun = "I"  # initialize w/ 1st person pronoun (default)
        value, unit = self.__patient.age
        if (unit in ["day", "week", "month"]) or (unit == "year" and value <= 10):  # Pediatric cases
            if self.__patient.gender == "male":  # male patient
                pronoun = "he"
            else:  # female patient
                pronoun = "she"

        # (2) Modify verb based on pronoun:
        if (verb == "am") and (pronoun != "I"):
            verb = "is"
        elif (verb == "have") and (pronoun != "I"):
            verb = "has"
        elif (verb == "do") and (pronoun != "I"):
            verb = "does"
        elif verb == "had":
            pass  # no modification for pronoun needed
        elif pronoun != "I":  # catchall for other verbs
            verb += "s"  # add s to end (e.g. I take vs. he takes)
        return (pronoun, verb)  # return TUPLE w/ (pronoun, verb)

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
            self.__patient.logQueryData(self.__activity.getConversationID(), self.__query)  # log query
            self.__patient.logError(self.__activity.getConversationID(), "LUIS_ERROR: {}".format(response.error))
            self.__activity.sendTextMessage(text="Sorry, I didn't understand that. "
                                                 "Please try rephrasing your question.")  # send error msg
        else:  # successful web request - get intents & entities for user input
            json_data = json.loads(response.body.decode('utf-8'))  # get JSON dict from HTTP body
            altered_query = json_data.get('alteredQuery', None)  # if spell check alters the query
            self.__topIntent = Intent(json_data.get('topScoringIntent', None))  # access the HIGHEST probability intent
            self.__intents = [Intent(i) for i in json_data.get('intents', [])]  # access each intent & wrap it in class
            self.__entities = [Entity(e) for e in json_data.get('entities', [])]  # access each intent & wrap in class
            self.__patient.logQueryData(self.__activity.getConversationID(), self.__query, altered_query=altered_query,
                                        intents=self.__intents, entities=self.__entities)  # log query/LUIS response
            if altered_query:  # query was altered
                self.__query = altered_query  # *overwrite self.query AFTER logging data -> DB!*
            try:  # wrap in try statement so we can still remove blocker after server error
                self.renderResponseForQuery()
            except Exception as e:  # remove blocker on failure
                print("[{}] Unable to render response: <{}>".format(type(e), e))
                self.__patient.logError(self.__activity.getConversationID(), "{}: {}".format(type(e), e))
                self.__activity.sendTextMessage(text="Sorry, I didn't understand that. "
                                                     "Please try rephrasing your question.")  # send error msg

    def renderResponseForQuery(self):  # constructs a response based on the user's query intent
        # General tip - provide AS LITTLE information as possible with each response. Force user to ask right ?s
        self.__response = "Sorry, I didn't understand that" # (1) initialize default response message
        self.__scope = Scope(self.__patient.checkCurrentScope(self.__activity.getConversationID()))  # (2) init scope

        # (3) Check for a CLARIFICATION object:
        clarification = self.__patient.getCacheForClarification(self.__activity.getConversationID())
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

        # Objectives (V 1.0)
        # - 2) Determine how to CONNECT bot to the Facebook messenger channel (done via My Bots page)
        #      - refer to FB messenger documentation:
        #       https://developers.facebook.com/docs/messenger-platform/app-review
        #       https://developers.facebook.com/docs/messenger-platform/prelaunch-checklist
        #      - 1) Modify bot to comply w/ FB guidelines, bug reported for privacyURL, fixed?
        #      - 2) Submit fully compliant bot -> FB for publishing
        # - 3) Updating code w/o stopping server
        #       - we can update static files remotely, but doesn't look like we can remotely update code files

        # Improving Recognition Model:
        # - product similar to LUIS except you can manually control what factors the model takes into account
        # - e.g. word choice (explicit vs. entities), word order, distance between entities, etc.
        # - once selected, model is trained using specified factors?

        # Non-Historical Intents:
        if self.__topIntent.intent == "None":  # receptacle for unwanted questions ***
            pass
        elif self.__topIntent.intent == "Greeting":
            name = self.__activity.getUserName()  # check if user's name is defined
            self.__response = "Hello, Dr. {}".format(name[1]) if name else "Hello"
        elif self.__topIntent.intent == "GetName":  # asking for name
            self.__response = self.__patient.name

        # Recognizer Intents:
        elif self.__topIntent.intent == "RecognizerChiefComplaint":  # open CURRENT cc scope
            self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_CURRENT)
            pronoun, verb = self.modifyPronoun("have")
            self.__response = "{} {} been having some {}.".format(pronoun, verb, self.__patient.chief_complaint)
        elif self.__topIntent.intent == "RecognizerSurgery":  # intent for ANY procedure
            if self.findMatchingEntity("surgery", ["pap smear", "pap smears"]):  # GYN HX - pap smear
                if self.__patient.gynecologic_history:  # gyn history exists
                    self.__scope.switchScopeTo(new_scope=Scope.GYNECOLOGIC_HISTORY)  # open scope
                    self.__response = self.__patient.gynecologic_history.pap_smears  # provide response AS IS
                else:  # no gyn history
                    pronoun, verb, = self.modifyPronoun("have")
                    self.__response = "No, {} {} never had a pap smear".format(pronoun, verb)
            elif self.findMatchingEntity("surgery", ["ppd"]):  # social history - PPD
                ppd = self.__patient.social_history.ppd  # check if PPD property is defined
                self.__response = "No" if ppd is None else ppd
            elif self.findMatchingEntity("surgery", ["abortion", "abortion", "aborted"]):  # GYN HX - abortion
                if self.__patient.gynecologic_history:  # gyn history exists
                    self.__response = self.__patient.getBirthHistorySummary(scope=self.__scope)
            else:  # default -> SURGICAL history
                pronoun, verb = self.modifyPronoun("have")
                self.__response = ""  # clear existing text
                if len(self.__patient.surgical_history) == 0:  # no surgeries
                    if query_word == "have":  # YES/NO queries
                        self.__response += "No, "
                    self.__response += "{} {} never had surgery before.".format(pronoun, verb)
                else:  # at least 1 surgery - store surgery scope
                    self.__scope.switchScopeTo(Scope.SURGICAL_HISTORY)
                    if query_word == "have":  # YES/NO queries
                        self.__response += "Yes, "
                    self.__response += "{} {} had a {}.".format(pronoun, verb,
                                                                LUIS.joinWithAnd(self.__patient.getSurgeryList()))
        elif self.__topIntent.intent == "RecognizerAllergy":
            self.__response = ""  # clear existing text
            if len(self.__patient.allergies) == 0:  # no allergies
                pronoun, verb = self.modifyPronoun("do")
                if query_word in ["do", "does", "are", "is"]:  # YES/NO queries
                    self.__response += "No, "
                self.__response += "{} {} not have allergies.".format(pronoun, verb)
            else:  # at least 1 allergy - store allergy scope
                self.__scope.switchScopeTo(Scope.ALLERGIES)
                if query_word in ["do", "does", "are", "is"]:  # YES/NO queries
                    self.__response += "Yes, "
                pronoun, verb = self.modifyPronoun("am")
                self.__response += "{} {} allergic to {}.".format(pronoun, verb,
                                                                  LUIS.joinWithAnd(self.__patient.getAllergenList()))
        elif self.__topIntent.intent == "RecognizerSexualHistory":
            if self.__patient.social_history.sexual_history:  # sxh was provided - open scope
                self.__scope.switchScopeTo(Scope.SEXUAL_HISTORY)
                if len(self.findMatchingEntity("timeQualifier")) > 0:  # time qualifier provided - send a TIME response
                    if len(self.findMatchingEntity("timeQualifier", ["last", "most recent",
                                                                 "most recently"])) > 0:  # LAST active keywords
                        if self.__patient.social_history.sexual_history.last_active is not None:  # value exists
                            self.__response = "{}".format(self.__patient.social_history.sexual_history.last_active)
                        else:  # value was not given in history
                            self.__response = "I don't remember"
                    else:  # default query is about START AGE
                        if self.__patient.social_history.sexual_history.start_age is not None:  # optional value exists
                            self.__response = "At {}".format(self.__patient.social_history.sexual_history.start_age)
                        else:  # value was not given in history
                            self.__response = "I don't remember"
                else:  # no time qualifiers provided
                    if self.__patient.social_history.sexual_history.status == Substance.STATUS_ACTIVE:  # ACTIVE
                        self.__response = "Yes, I am sexually active"
                    elif self.__patient.social_history.sexual_history.status == Substance.STATUS_PREVIOUS:  # PREVIOUS
                        self.__response = "I have been in the past, but not currently"
                    elif self.__patient.social_history.sexual_history.status == Substance.STATUS_NEVER:  # NEVER
                        self.__response = "No, I've never been sexually active"
            else:  # no sxh provided - send default message
                self.__response = "I would prefer not to talk about that."
        elif self.__topIntent.intent == "GetDevelopmentalStatus":  # Pediatric patients - development
            if self.__patient.developmental_history.development is not None:
                self.__response = self.__patient.developmental_history.development  # return response as is

        # Cross-Element Intents:
        elif self.__topIntent.intent == "GetAge":  # ID, FH
            self.handleGetAgeIntent(query_word)
        elif self.__topIntent.intent == "GetCategory":  # HPI, medications
            self.handleGetCategoryIntent(query_word)
        elif self.__topIntent.intent == "GetComplications":  # past surgical history, birth history
            self.handleGetComplicationsIntent(query_word)
        elif self.__topIntent.intent == "GetDisease":  # PMH, FH
            self.handleGetDiseaseIntent(query_word)
        elif self.__topIntent.intent == "GetExposure":  # allergies, sick contacts
            self.handleGetExposureIntent(query_word)
        elif self.__topIntent.intent == "GetGender":  # ID, sexual history
            self.handleGetGenderIntent(query_word)
        elif self.__topIntent.intent == "GetIndication":  # surgeries, medications
            self.handleGetIndicationIntent(query_word)
        elif self.__topIntent.intent == "GetLocation": # PMH, travel history
            self.handleGetLocationIntent(query_word)
        elif self.__topIntent.intent == "GetQuantifier":  # social history
            self.handleGetQuantifierIntent(query_word)
        elif self.__topIntent.intent == "GetTime":  # HPI, associated symptoms
            self.handleGetTimeIntent(query_word)  # pass -> handler
        elif self.__topIntent.intent == "GetTreatment":  # HPI, PMH, medications, FH, substance history
            self.handleGetTreatmentIntent(query_word)

        # HPI Intents:
        elif self.__topIntent.intent == "GetModifiableFactors":  # anything worsen or improve the problem?
            self.handleGetModifiableIntent(query_word)
        elif self.__topIntent.intent == "GetPrecipitant":  # what initiated the chief complaint?
            self.handleGetPrecipitantIntent(query_word)
        elif self.__topIntent.intent == "GetPrevious":  # any previous instances of the chief complaint?
            self.handleGetPreviousIntent(query_word)
        elif self.__topIntent.intent == "GetProgression":  # have symptoms changed since they started?
            self.handleGetProgressionIntent(query_word)
        elif self.__topIntent.intent == "GetSeverity":  # how severe are the symptoms?
            self.handleGetSeverityIntent(query_word)
        elif self.__topIntent.intent == "GetSymptoms":  # any symptoms associated with chief complaint?
            self.handleGetSymptomsIntent(query_word)

        # Social History Intents:
        elif self.__topIntent.intent == "GetHousing":  # question about housing
            self.__scope.switchScopeTo(Scope.SOCIAL_HISTORY)
            self.__response = self.__patient.social_history.housing
        elif self.__topIntent.intent == "GetEmployment":  # question about employment
            self.__scope.switchScopeTo(Scope.SOCIAL_HISTORY)
            pronoun, verb = self.modifyPronoun("am")
            self.__response = "{} {} a {}".format(pronoun, verb, self.__patient.social_history.employment)
        elif self.__topIntent.intent == "GetDiet":  # question about diet
            self.__scope.switchScopeTo(Scope.SOCIAL_HISTORY)
            self.__response = self.__patient.social_history.diet
        elif self.__topIntent.intent == "GetExercise":  # question about exercise
            self.__scope.switchScopeTo(Scope.SOCIAL_HISTORY)
            self.__response = self.__patient.social_history.exercise
        elif self.__topIntent.intent == "GetTravelMode":  # what transportation was used for a given trip
            if self.__scope.isScope(Scope.TRAVEL_HISTORY):  # make sure travel scope is open
                travel = self.identifyObject("travel") # get index for the location
                if travel is not None:
                    self.__response = travel.mode  # return response as is
                else:  # no match found - apply clarification logic
                    self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                              self.__entities, "builtin.geography")
                    self.__response = "Which trip are you referring to?"

        # (LAST) Persist the scope object & then render the bot's response:
        self.__patient.persistCurrentScope(self.__activity.getConversationID(), self.__scope.getScopeForDB())
        self.__activity.sendTextMessage(text="{}".format(self.__response))  # send msg

    # --- <CROSS-ELEMENT> INTENT HANDLERS ---
    def handleGetAgeIntent(self, query_word):  # "GET_AGE" intent
        if self.__scope.isScope(Scope.BIRTH_HISTORY) or \
                        len(self.findMatchingEntity("recognizerKeywords", ["birth", "born"])) > 0:  # birth history
            if not self.__scope.isScope(Scope.BIRTH_HISTORY):  # IFF birth history is NOT current scope...
                self.__scope.switchScopeTo(Scope.BIRTH_HISTORY)  # open Birth History scope
            birth = self.identifyObject("birth")  # get birth object
            if birth is not None:
                self.__response = "{} weeks".format(birth.gestational_age)
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

        elif self.__scope.isScope(Scope.FAMILY_HISTORY) or \
                        len(self.findMatchingEntity("relationship", ["father", "dad", "mother", "mom"])) > 0:
            # Return fm's current health status vs. age of death
            if len(self.__patient.family_history) > 0:  # FH was given (OPTIONAL property)
                if not self.__scope.isScope(Scope.FAMILY_HISTORY):  # (1) check if FH scope is open
                    self.__scope.switchScopeTo(Scope.FAMILY_HISTORY)  # if not, open it (nest to avoid clearing element)
                fm = self.identifyObject("family")  # THEN get the fm object
                if fm is not None:
                    if (fm.cause_of_death):  # fm is DECEASED
                        self.__response = "My {} passed away at {} from {}".format(fm.relationship, fm.age,
                                                                                   fm.cause_of_death)
                    else:  # fm is ALIVE
                        self.__response = "{} is {}".format(fm.getGenderPronoun().capitalize(), fm.age)
                else:  # failure to ID - ask for clarification
                    self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                              self.__entities, "relationship")
                    self.__response = "Which family member are you referring to?"
            else:  # no FH
                self.__response = "I don't know"

        else:  # default - query about PATIENT's age
            unit = LUIS.pluralize(self.__patient.age[1], self.__patient.age[0])
            pronoun, verb = self.modifyPronoun("am")
            self.__response = "{} {} {} {} old.".format(pronoun, verb, self.__patient.age[0], unit)  # return age

    def handleGetCategoryIntent(self, query_word):  # "GET_CATEGORY" intent
        if self.findMatchingEntity("recognizerKeywords", ["menses", "period", "periods", "menstrual period",
                                                          "menstrual periods", "cycle", "cycles", "menstrual cycle",
                                                          "menstrual cycles"]):  # Gyn Hx - cycle description
            if self.__patient.gynecologic_history:  # gyn history exists
                self.__scope.switchScopeTo(Scope.GYNECOLOGIC_HISTORY)  # open scope
                self.__response = self.__patient.gynecologic_history.cycles  # return value AS IS
            else: self.__response = "I'd rather not talk about that right now"  # empty response

        elif self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_CURRENT) or \
                self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_PREVIOUS) or \
                        len(self.findMatchingEntity(of_type="symptom")) > 0:  # query about quality of CC symptom
            symptom = self.getSymptomForQuery()
            if (symptom.quality): self.__response = symptom.quality  # OPTIONAL property

        elif self.__scope.isScope(Scope.BIRTH_HISTORY) or \
            len(self.findMatchingEntity("recognizerKeywords", ["birth", "born"])) > 0:  # birth history
            if not self.__scope.isScope(Scope.BIRTH_HISTORY):  # IFF birth history is NOT current scope...
                self.__scope.switchScopeTo(Scope.BIRTH_HISTORY)  # open Birth History scope
            birth = self.identifyObject("birth")
            if birth is not None:
                if (birth.delivery_method): self.__response = birth.delivery_method  # OPTIONAL property
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

        elif self.__scope.isScope(Scope.MEDICATIONS):  # medication category
            med = self.identifyObject("medication")  # get medication index
            if med is not None:
                self.__response = med.category
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "medication")
                self.__response = "Which medication are you referring to?"

    def handleGetComplicationsIntent(self, query_word):  # "GET_COMPLICATIONS" intent
        if self.__scope.isScope(Scope.BIRTH_HISTORY) or \
                        len(self.findMatchingEntity("recognizerKeywords", ["birth", "born"])) > 0:  # birth complication
            if not self.__scope.isScope(Scope.BIRTH_HISTORY):  # IFF birth history is NOT current scope...
                self.__scope.switchScopeTo(Scope.BIRTH_HISTORY)  # open Birth History scope
            birth = self.identifyObject("birth")
            if birth is not None:
                if len(birth.complications) > 0:  # check if there were complications
                    self.__response = LUIS.joinWithAnd(birth.complications)
                else:
                    self.__response = "No"
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

        elif self.__scope.isScope(scope=Scope.SURGICAL_HISTORY):  # surgical complications
            surgery = self.identifyObject("surgery")  # get surgery index
            if surgery is not None:
                if (surgery.complications):  # complications were given for surgery
                    self.__response = surgery.complications
                else:  # no complications noted
                    self.__response = "No"
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "surgery")
                self.__response = "Which surgery are you referring to?"

    def handleGetDiseaseIntent(self, query_word):  # "GET_DISEASE" intent
        if len(self.findMatchingEntity("recognizerKeywords", ["family"])) > 0:  # FAMILY history RECOGNIZER
            self.__response = self.__patient.getFamilyHistorySummary(self.__scope)  # return result of built-in method

        elif len(self.findMatchingEntity("disease", ["pregnant", "pregnancy", "pregnancies", "gave birth",
                                                     "given birth", "delivered", "miscarriage", "miscarriages",
                                                     "ectopic pregnancy", "ectopic pregnancies", "ectopic"])) > 0:
            # BIRTH history RECOGNIZER
            if self.__patient.gynecologic_history:
                self.__response = self.__patient.getBirthHistorySummary(self.__scope)
            else:  # no gyn history provided
                self.__response = "No"

        elif self.__scope.isScope(Scope.FAMILY_HISTORY) or \
                        len(self.findMatchingEntity("relationship", ["father", "dad", "mother", "mom"])) > 0:
            # query about specific family member's medical problems
            if len(self.__patient.family_history) > 0:  # FH was given (OPTIONAL property)
                if not self.__scope.isScope(Scope.FAMILY_HISTORY):  # (1) check if FH scope is open
                    self.__scope.switchScopeTo(Scope.FAMILY_HISTORY)  # if not, open it (nest to avoid clearing element):
                fm = self.identifyObject("family")  # (2) get the fm's index
                if fm is not None:
                    if len(fm.conditions) == 0:  # fm had no medical problems
                        self.__response = "My {} is healthy".format(fm.relationship)
                    else:  # fm had at least 1 medical problem
                        if (fm.cause_of_death):  # fm is DECEASED
                            self.__response = "{} had {}".format(fm.getGenderPronoun().capitalize(),
                                                                 LUIS.joinWithAnd(fm.conditions))
                        else:  # fm is ALIVE
                            self.__response = "My {} has {}".format(fm.relationship, LUIS.joinWithAnd(fm.conditions))
                else:  # failure to ID - ask for clarification
                    self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                              self.__entities, "relationship")
                    self.__response = "Which family member are you referring to?"
            else:  # no family history
                self.__response = "I don't know"

        else:  # default to PAST MEDICAL HISTORY
            pronoun, verb = self.modifyPronoun("have")
            self.__response = ""  # clear existing text
            if len(self.__patient.medical_history) == 0:  # no PMH
                if query_word in ["do", "does", "have", "has"]:  # YES/NO queries
                    self.__response += "No, "
                self.__response += "{} {} never been diagnosed with a medical condition.".format(pronoun, verb)
            else:  # at least 1 diagnosed condition - store PMH scope
                self.__scope.switchScopeTo(Scope.MEDICAL_HISTORY)
                if query_word in ["do", "does", "have", "has"]:  # YES/NO queries
                    self.__response += "Yes - "
                active, resolved = self.__patient.getDiagnosisList()  # break up tuple
                if len(active) > 0:  # patient has ACTIVE illnesses
                    self.__response += "{} currently {} {}.".format(pronoun, verb, LUIS.joinWithAnd(active))
                if len(resolved) > 0:  # patient has some RESOLVED conditions
                    self.__response += "in the past, {} {} had {}.".format(pronoun, verb, LUIS.joinWithAnd(resolved))

    def handleGetExposureIntent(self, query_word):  # "GET_EXPOSURE" intent
        if self.__scope.isScope(scope=Scope.ALLERGIES):  # allergic reactions
            allergy = self.identifyObject("allergy")  # get allergy index
            if allergy is not None:  # allergy was ID'd
                self.__response = allergy.reaction
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "allergy")
                self.__response = "Which allergy are you referring to?"
        else:  # default to SICK CONTACTS
            contacts = self.__patient.social_history.sick_contacts[
                       :] if self.__patient.social_history.sick_contacts is not None else None  # pass list by COPY
            daycare_query = False  # indicator for query about daycare
            daycare_element = None  # indicates INDEX of daycare sick contact if it exists
            if len(self.findMatchingEntity("recognizerKeywords", ["daycare", "day care", "day - care"])) > 0:
                daycare_query = True  # set indicator - query is about DAY CARE
            if contacts is not None:  # sick contacts EXIST
                for i, ct in enumerate(contacts):  # check each sick contact in list for daycare contact
                    if ("daycare" in ct) or ("day care" in ct) or ("day-care" in ct):  # daycare contact found
                        daycare_element = i  # store index in list

            # Construct response depending on the QUERY type:
            if daycare_query:  # query about daycare
                if daycare_element is None:  # no daycare
                    self.__response = "No"
                else:  # patient DOES attend daycare
                    self.__response = contacts[daycare_element]  # return daycare contact
            else:  # general sick contact query
                if contacts is None:  # no sick contacts
                    self.__response = "Not that I know of."
                else:  # sick contacts exist
                    if daycare_element is not None:  # daycare element exists
                        del(contacts[daycare_element])  # *FIRST remove element from response!*
                    self.__response = ". ".join(contacts)  # concatenate each remaining element

    def handleGetGenderIntent(self, query_word):  # "GET_GENDER" intent
        if self.__scope.isScope(scope=Scope.SEXUAL_HISTORY):  # get gender of sexual partners
            partners = self.__patient.social_history.sexual_history.partner_type
            if (partners):  # partner gender was provided
                if len(partners) == 1:  # active w/ 1 gender
                    self.__response = "men" if partners[0] == "male" else "women"  # return the single gender
                elif len(partners) == 2:  # active w/ BOTH genders
                    self.__response = "both"

        elif self.__scope.isScope(Scope.BIRTH_HISTORY):  # Birth history - gender
            birth = self.identifyObject("birth")
            if birth is not None:
                if (birth.gender): self.__response = "A {}".format(birth.gender)  # OPTIONAL
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

        else:  # default -> PATIENT's gender
            pronoun, verb = self.modifyPronoun("am")
            self.__response = "{} {} a {}.".format(pronoun, verb, self.__patient.gender)

    def handleGetIndicationIntent(self, query_word):  # "GET_INDICATION" intent
        if self.__scope.isScope(Scope.MEDICATIONS):  # reason for taking medication
            med = self.identifyObject("medication")  # get medication index
            if med is not None:  # medication was ID'd
                self.__response = med.indication
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "medication")
                self.__response = "Which medication are you referring to?"

        elif self.__scope.isScope(Scope.SURGICAL_HISTORY):  # reason for surgery
            surgery = self.identifyObject("surgery")  # get surgery index
            if surgery is not None:  # match found
                self.__response = surgery.indication
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "surgery")
                self.__response = "Which surgery are you referring to?"

        elif self.__scope.isScope(Scope.BIRTH_HISTORY):  # Birth history - indication for C-section
            birth = self.identifyObject("birth")
            if birth is not None:
                if (birth.indication): self.__response = birth.indication  # OPTIONAL property
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

    def handleGetLocationIntent(self, query_word):  # "GET_LOCATION" intent
        if len(self.findMatchingEntity("recognizerKeywords", ["you travel", "you traveled",
                                                              "visit", "visited"])) > 0:  # TRAVEL HX
            self.__response = self.__patient.getTravelHistorySummary()  # use built-in response

        elif self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_CURRENT) or \
                self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_PREVIOUS) or \
                        len(self.findMatchingEntity(of_type="symptom")) > 0:  # query about location OR radiation
            symptom = self.getSymptomForQuery()
            if symptom is not None:  # use the ENTITY to differentiate queries & render a response
                if len(self.findMatchingEntity("recognizerKeywords", ["radiate", "travel",
                                                                      "move"])) > 0:  # RADIATION query
                    rad = symptom.radiation  # get the radiation LIST
                    self.__response = "To my {}".format(LUIS.joinWithAnd(rad)) if rad else "No"
                else:  # default -> LOCATION query if no radiation query was found
                    if (symptom.location): self.__response = symptom.location  # OPTIONAL property

    def handleGetQuantifierIntent(self, query_word):  # "GET_QUANTIFIER" intent
        if len(self.findMatchingEntity("recognizerKeywords", ["exercise", "physical activity"])) > 0:  # SH
            self.__scope.switchScopeTo(Scope.SOCIAL_HISTORY)
            self.__response = self.__patient.social_history.exercise

        elif len(self.findMatchingEntity("recognizerKeywords", ["diaper", "diapers"])) > 0:  # Dev Hx > wet diapers
            if self.__patient.developmental_history is not None:
                self.__response = self.__patient.developmental_history.wet_diapers

        elif self.findMatchingEntity("recognizerKeywords", ["menses", "period", "periods", "menstrual period",
                                                            "menstrual periods", "cycle", "cycles", "menstrual cycle",
                                                            "menstrual cycles"]):  # Gyn - "how far apart are cycles?"
            if self.__patient.gynecologic_history:  # gyn history exists
                self.__scope.switchScopeTo(Scope.GYNECOLOGIC_HISTORY)  # open scope
                self.__response = self.__patient.gynecologic_history.cycles  # return value AS IS
            else: self.__response = "I'd rather not talk about that right now"  # empty response

        elif self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_CURRENT) or \
                self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_PREVIOUS) or \
                        len(self.findMatchingEntity(of_type="symptom")) > 0:  # CC - frequency vs. duration of symptom
            symptom = self.getSymptomForQuery()
            if symptom is not None:  # use the query to differentiate between intents
                if query_word == "how long":  # duration query
                    if symptom.duration is not None:  # duration IS provided
                        self.__response = symptom.duration
                    else:  # if NO duration is present, default -> ONSET (but modify the string)
                        index = symptom.onset.find("ago")  # remove the 'ago' portion of the response
                        self.__response = symptom.onset[:index] if index > 0 else symptom.onset
                    # else:  # if NO duration is provided, default -> PROGRESSION (seems to capture similar intent)
                    #     self.__response = symptom.progression
                    # ** what works better - onset or progression as fallback? **
                elif query_word in ["how often", "how frequently", "how many"] and (symptom.frequency is not None):
                    self.__response = symptom.frequency
                elif symptom.quantity is not None:  # catch-all for "Quantity" field of certain symptoms (e.g. fever)
                    self.__response = symptom.quantity  # return the value as is
                else:  # default when "quantity" is not defined (e.g. T_max for fever is unknown)
                    self.__response = "I don't know"

        elif self.__scope.isScope(Scope.BIRTH_HISTORY) or \
            len(self.findMatchingEntity("recognizerKeywords", ["born", "birth"])) > 0:  # birth history
            if not self.__scope.isScope(Scope.BIRTH_HISTORY):  # IFF birth history is NOT current scope...
                self.__scope.switchScopeTo(Scope.BIRTH_HISTORY)  # open Birth History scope
            birth = self.identifyObject("birth")
            if birth is not None:
                if self.findMatchingEntity("timeQualifier", ["weeks"]):  # check if time indicator is present
                    self.__response = "{} weeks".format(birth.gestational_age)
                else:  # default -> BIRTH WEIGHT query
                    if birth.birth_weight is not None:  # OPTIONAL
                        lb, oz = birth.birth_weight
                        self.__response = "{} was {} pounds, {} ounces".format(birth.getGenderPronoun(), lb, oz)
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                            self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

        elif self.__scope.isScope(Scope.MEDICAL_HISTORY):  # disease - diagnosis date
            disease = self.identifyObject("disease")
            if disease is not None:  # match found
                unit = LUIS.pluralize(disease.duration_units, disease.duration)
                self.__response = "{} {}".format(disease.duration, unit)  # default response for 'how long' query
                if disease.status == Substance.STATUS_PREVIOUS: self.__response += " ago"  # append 'ago' onto response
            else:  # no match found
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "disease")
                self.__response = "Which diagnosis are you referring to?"

        elif self.__scope.isScope(Scope.SURGICAL_HISTORY):  # surgery date
            surgery = self.identifyObject("surgery")
            if surgery is not None:  # match found
                self.__response = surgery.date  # return the date as specified
            else:  # no match found
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "surgery")
                self.__response = "Which surgery are you referring to?"

        elif self.__scope.isScope(Scope.MEDICATIONS):  # medication use - amount
            med = self.identifyObject("medication")
            if med is not None:  # index was found
                unit = LUIS.pluralize(med.dose_unit, med.dose_amount)
                route = med.dose_route
                route += "ly" if med.dose_route == "oral" else ""  # append ly -> 'orally'
                self.__response = "{} {} {} {}".format(med.dose_amount, unit, route, med.dose_rate)
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "medication")
                self.__response = "Which medication are you referring to?"

        elif self.__scope.isScope(Scope.SUBSTANCES):  # substance use - duration vs. amount
            substance = self.identifyObject("substance")
            if substance is not None:  # index was found
                if query_word == "how long":  # DURATION query
                    if len(self.findMatchingEntity("timeQualifier", ["quit", "stop", "last", "stopped"])) > 0:
                        if substance.last_use is not None:  # how long since patient QUIT or LAST used substance
                            self.__response = substance.last_use # respond w/ TIME REFERENCE
                            return  # terminate to avoid catch-all
                    elif len(self.findMatchingEntity("timeQualifier", ["first", "start", "started"])) > 0:
                        if substance.age_of_first_use is not None: # how long since patient FIRST used substance
                            self.__response = "I was {} when I started".format(substance.age_of_first_use)
                            return  # terminate to avoid catch-all
                    elif substance.duration_value is not None:  # default -> DURATION if it is defined (optional item)
                        unit = LUIS.pluralize(substance.duration_units, substance.duration_value)
                        self.__response = "{} {}".format(substance.duration_value, unit)
                        return # terminate to avoid catch-all
                    self.__response = "I'm not sure"  # catch-all
                else:  # default - AMOUNT query
                    if substance.amount_value is not None:  # amount was found
                        unit = LUIS.pluralize(substance.amount_units, substance.amount_value)
                        self.__response = "{} {} {}".format(substance.amount_value, unit, substance.amount_rate)
                    else:  # no amount given - default response
                        self.__response = "None"

        elif self.__scope.isScope(Scope.TRAVEL_HISTORY):  # travel query - length of trip
            trip = self.identifyObject("travel")  # get index for the location
            if trip is not None:
                self.__response = "I left on {} and got back on {}".format(trip.departure_date, trip.return_date)
            else:  # no match found
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "builtin.geography")
                self.__response = "Which trip are you referring to?"

        elif self.__scope.isScope(Scope.SEXUAL_HISTORY):  # number of partners (current/past year/lifetime)
            # Use "timeQualifier" entity to sort between queries:
            if len(self.findMatchingEntity("timeQualifier", ["lifetime", "life time"])) > 0: # OPTIONAL item
                if self.__patient.social_history.sexual_history.partners_lifetime is not None:  # value exists
                    self.__response = "{}".format(self.__patient.social_history.sexual_history.partners_lifetime)
                else:  # value was not given
                    self.__response = "I don't remember"
            elif len(self.findMatchingEntity("timeQualifier", ["past year", "this year", "year"])) > 0: # OPTIONAL
                if self.__patient.social_history.sexual_history.partners_past_year is not None:  # value exists
                    self.__response = "{}".format(self.__patient.social_history.sexual_history.partners_past_year)
                else:  # value was not given
                    self.__response = "I don't remember"
            elif len(self.findMatchingEntity("timeQualifier", ["current", "currently"])) > 0:  # REQUIRED item
                self.__response = "{}".format(self.__patient.social_history.sexual_history.partners_current)
            else:  # default - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Over what time frame?"

    def handleGetTimeIntent(self, query_word):  # "GET_TIME" intent
        if self.findMatchingEntity("recognizerKeywords", ["menses", "period", "periods", "menstrual period",
                                                          "menstrual periods", "cycle", "cycles", "menstrual cycle",
                                                          "menstrual cycles"]):  # GYN - lmp vs. menarche
            if self.__patient.gynecologic_history:  # gyn history exists
                self.__scope.switchScopeTo(Scope.GYNECOLOGIC_HISTORY)  # open gyn history scope if object exists
                if self.findMatchingEntity("timeQualifier", ["first"]):  # query about age of menarche
                    self.__response = "I was {} years old".format(self.__patient.gynecologic_history.age_of_menarche)
                else:  # default -> LMP query
                    self.__response = self.__patient.gynecologic_history.lmp
            else:  # no gyn history provided
                self.__response = "Sorry, I don't remember."

        elif self.__patient.developmental_history is not None and \
                len(self.findMatchingEntity("recognizerKeywords", ["checkup", "check - up", "check up", "visit"])) > 0:
            self.__response = self.__patient.developmental_history.last_checkup  # Dev Hx > last checkup

        elif self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_CURRENT) or \
                self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_PREVIOUS) or \
                        len(self.findMatchingEntity(of_type="symptom")) > 0:  # chief complaint - ONSET
            symptom = self.getSymptomForQuery()
            if symptom is not None: self.__response = symptom.onset

        elif self.__scope.isScope(Scope.BIRTH_HISTORY) or \
            len(self.findMatchingEntity("recognizerKeywords", ["born", "birth"])) > 0:  # birth history
            if not self.__scope.isScope(Scope.BIRTH_HISTORY):  # IFF birth history is NOT current scope...
                self.__scope.switchScopeTo(Scope.BIRTH_HISTORY)  # open Birth History scope
            birth = self.identifyObject("birth")
            if birth is not None:
                if self.findMatchingEntity("relationship", ["baby", "child", "son", "daughter"]):  # GA
                    self.__response = "{} was {} weeks".format(birth.getGenderPronoun(), birth.gestational_age)
                else:  # default -> MATERNAL age
                    self.__response = "I was {}".format(birth.maternal_age)
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

        elif self.__scope.isScope(Scope.MEDICAL_HISTORY):  # disease - diagnosis date
            disease = self.identifyObject("disease")
            if disease is not None:  # match found
                unit = LUIS.pluralize(disease.duration_units, disease.duration)
                self.__response = "{} {}".format(disease.duration, unit)  # default response for 'how long' query
                if query_word == "when": self.__response += " ago"  # append 'ago' onto response
            else:  # no match found
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "disease")
                self.__response = "Which diagnosis are you referring to?"

        elif self.__scope.isScope(Scope.SURGICAL_HISTORY):  # surgery date
            surgery = self.identifyObject("surgery")
            if surgery is not None:  # match found
                self.__response = surgery.date  # return the date as specified
            else:  # no match found - apply clarification logic
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "surgery")
                self.__response = "Which surgery are you referring to?"

        elif self.__scope.isScope(Scope.SUBSTANCES):  # substance use
            substance = self.identifyObject("substance")
            if substance is not None:  # index found
                self.__response = "I don't remember"  # default response if history was not provided

                # Use entities to parse out the specific query type:
                last_query = True if len(self.findMatchingEntity("timeQualifier", ["quit", "stop", "last",
                                                                                        "stopped"])) > 0 else False
                first_query = True if (len(self.findMatchingEntity("timeQualifier", ["first", "start",
                                                                                     "started"])) > 0) else False
                if first_query and (substance.age_of_first_use is not None): # how old when patient FIRST used
                    self.__response = "I was {} when I started".format(substance.age_of_first_use)
                elif last_query: # when did patient QUIT or LAST use substance
                    if (query_word == "how old") and (substance.last_use is not None):  # respond w/ an AGE
                        self.__response = "It was {}. I'm not sure exactly how old I was.".format(substance.last_use)
                    elif (query_word == "when") and (substance.last_use is not None):  # respond w/ TIME REFERENCE
                        self.__response = substance.last_use

        elif self.__scope.isScope(Scope.TRAVEL_HISTORY):  # travel query - departure/return dates
            travel = self.identifyObject("travel")  # get travel location
            if travel is not None:
                if len(self.findMatchingEntity("recognizerKeywords", ["leave", "depart"])) > 0:  # departure keywords
                    self.__response = travel.departure_date
                elif len(self.findMatchingEntity("recognizerKeywords", ["return", "get back"])) > 0:  # return keywords
                    self.__response = travel.return_date
            else:  # no match found - apply clarification logic
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "builtin.geography")
                self.__response = "Which trip are you referring to?"

        elif self.__scope.isScope(Scope.SEXUAL_HISTORY):  # start age vs. last active
            if len(self.findMatchingEntity("timeQualifier", ["last", "most recent"])) > 0:  # LAST active keywords
                if self.__patient.social_history.sexual_history.last_active is not None:  # optional value exists
                    self.__response = "{}".format(self.__patient.social_history.sexual_history.last_active)
                else:  # value was not given in history
                    self.__response = "I don't remember"
            else:  # default query is about START AGE
                if self.__patient.social_history.sexual_history.start_age is not None:  # optional value exists
                    self.__response = ""  # initialize
                    if query_word != "how old":
                        self.__response += "At "  # pre-pend 'at' to response
                    self.__response += "{}".format(self.__patient.social_history.sexual_history.start_age)
                else:  # value was not given in history
                    self.__response = "I don't remember"

    def handleGetTreatmentIntent(self, query_word):  # "GET_TREATMENT" intent
        if len(self.findMatchingEntity("recognizerKeywords", ["vaccine", "vaccines", "vaccination",
                                                              "vaccinations"])) > 0:  # vaccination status
            if self.__patient.developmental_history is not None:  # Dev Hx exists!
                self.__response = self.__patient.developmental_history.vaccinations

        elif len(self.findMatchingEntity("recognizerKeywords", ["medication", "medications", "medicine",
                                                                "medicines", "pill", "pills", "supplement",
                                                                "supplements"])) > 0 and \
                ("do" in self.__query or "does" in self.__query):  # ACTIVE query (do/does) if for medication list
            self.__response = ""  # clear existing text
            if len(self.__patient.medications) == 0:  # no medications
                pronoun, verb = self.modifyPronoun("do")
                if query_word in ["do", "does"]:  # YES/NO queries
                    self.__response += "No, "
                self.__response += "{} {} not take any medications.".format(pronoun, verb)
            else:  # at least 1 medication - open medication scope & generate medication list
                self.__scope.switchScopeTo(Scope.MEDICATIONS)
                pronoun, verb = self.modifyPronoun("take")
                if query_word in ["do", "does"]:  # YES/NO queries
                    self.__response += "Yes, "
                self.__response += "{} currently {} {}.".format(pronoun, verb,
                                                                LUIS.joinWithAnd(self.__patient.getMedicationList(),
                                                                                 prefix=True))

        elif self.__scope.isScope(Scope.SEXUAL_HISTORY) and \
            len(self.findMatchingEntity("medication", ["contraception", "contraceptive methods",
                                                       "birth control", "protection", "iud", "iuds",
                                                       "ocp", "ocps", "condom", "condoms"])) > 0:  # contraception
            if self.__patient.social_history.sexual_history.contraception is not None:  # value exists
                self.__response = "I use {}".format(LUIS.joinWithAnd(self.__patient.social_history.sexual_history.contraception))
            else:  # value was not given - assume NO contraception
                self.__response = "No"

        elif len(self.findMatchingEntity("medication")) > 0:  # query about a SPECIFIC medication
            if len(self.__patient.medications) == 0:  # no medications
                self.__response = "No"
            else:  # at least 1 medication - check for each medication in array of meds
                self.__scope.switchScopeTo(Scope.MEDICATIONS)  # set medication scope
                med_names = self.findMatchingEntity("medication")  # grab all medications
                matches = list()  # init list of MATCHING medications
                med_list = [da for m in self.__patient.getMedicationList()
                            for da in self.__patient.disambiguate("medication", m.lower())]  # d/a list of all meds
                for name in med_names:  # check EACH query med
                    if name in med_list:
                        matches.append(name)  # add med name -> return object

                if len(matches) == 1:  # only 1 medication in query (possible nesting)
                    medication = self.__patient.getObject("medication", matches[0])  # lookup object
                    if medication is not None:  # medication successfully identified - preserve in scope
                        self.__scope.switchScopeTo(medication.name.lower())  # set element -> CANONICAL medication name

                self.__response = ""  # clear & initialize
                if len(matches) > 0:  # medication was NOT ID'd
                    pronoun, verb = self.modifyPronoun("take")
                    self.__response += "Yes, {} {} {}. ".format(pronoun, verb, LUIS.joinWithAnd(matches))
                if len(matches) != len(med_names):  # some NON-matches found
                    pronoun, verb = self.modifyPronoun("do")
                    self.__response += "No, {} {} not take {}.".format(pronoun, verb,
                                                                       LUIS.joinWithAnd([m for m in med_names
                                                                                         if m not in matches],
                                                                                        joiner="or"))

        elif len(self.findMatchingEntity(of_type="substance")) > 0:  # substance was found
            self.__scope.switchScopeTo(Scope.SUBSTANCES)  # open scope
            if len(self.findMatchingEntity("substance", ["alcohol", "drink"])) > 0:  # alcohol
                self.__scope.switchScopeTo(Substance.ALCOHOL)  # store element in scope
                alcohol = self.lookupObjectForScope()
                if alcohol.status == Substance.STATUS_ACTIVE:  # active
                    self.__response = "Yes, I do drink alcohol"
                elif alcohol.status == Substance.STATUS_PREVIOUS:  # previous
                    self.__response = "I used to drink alcohol, but I quit"
                elif alcohol.status == Substance.STATUS_NEVER:  # never
                    pronoun, verb = self.modifyPronoun("drink")
                    self.__response = "No, {} never {} alcohol".format(pronoun, verb)
            elif len(self.findMatchingEntity("substance", ["smoke", "cigarettes", "tobacco", "smoker", "smoked"])) > 0 \
                    and \
                (len(self.findMatchingEntity("substance")) ==
                 len(self.findMatchingEntity("substance", ["smoke", "cigarettes", "tobacco", "smoker", "smoked"]))):
                # "SMOKE" can refer to use of cigarettes OR other drugs (like MJ)
                # To determine the intention, compare the # of 'substances' found to # of tobacco-related substances
                # If 'smoke' is either found ALONE or together w/ a TOBACCO keyword, stay here.
                # If 'smoke' is found w/ another substance keyword, move -> 'other' section
                self.__scope.switchScopeTo(Substance.TOBACCO)  # store element in scope
                tobacco = self.lookupObjectForScope()
                if tobacco.status == Substance.STATUS_ACTIVE:  # active
                    self.__response = "Yes, I smoke"
                elif tobacco.status == Substance.STATUS_PREVIOUS:  # previous
                    self.__response = "I used to smoke, but I quit"
                elif tobacco.status == Substance.STATUS_NEVER:  # never
                    pronoun, verb = self.modifyPronoun("have")
                    self.__response = "No, {} {} never smoked".format(pronoun, verb)
            else:  # handle all other substances here
                generics = set(self.findMatchingEntity("substance", ["drug", "drugs", "substance", "substances"]))
                all_substances = set(self.findMatchingEntity("substance"))  # get set of ALL substance entities
                specifics = all_substances - generics  # list of SPECIFIC substance entities
                if len(specifics) == 0:  # NO specific substances - utilize summary drug list
                    drug_list = self.__patient.getRecreationalDrugList()
                    if len(drug_list[Substance.STATUS_ACTIVE]) > 0 and len(drug_list[Substance.STATUS_PREVIOUS]) > 0:
                        self.__response = "I currently use {}. " \
                                          "I used to use {}.".format(LUIS.joinWithAnd(drug_list[Substance.STATUS_ACTIVE]),
                                                                     LUIS.joinWithAnd(drug_list[Substance.STATUS_PREVIOUS]))
                    elif len(drug_list[Substance.STATUS_ACTIVE]) > 0:  # only ACTIVE drug use
                        self.__response = "I use {}".format(LUIS.joinWithAnd(drug_list[Substance.STATUS_ACTIVE]))
                    elif len(drug_list[Substance.STATUS_PREVIOUS]) > 0:  # only PREVIOUS drug use
                        self.__response = "I used to use {}".format(LUIS.joinWithAnd(drug_list[Substance.STATUS_PREVIOUS]))
                    else:  # no active or previous substances
                        self.__response = "I don't use any recreational drugs"
                else:  # at least 1 SPECIFIC substance
                    substance_use = dict()  # used to render responses
                    for substance_name in specifics:  # iterate through list of SPECIFIC substances
                        substance = self.__patient.getObject("substance", substance_name)  # lookup object
                        if substance is None:  # object NOT found - default to NO response
                            if not Substance.STATUS_NEVER in substance_use:
                                substance_use[Substance.STATUS_NEVER] = list()  # initialize
                            substance_use[Substance.STATUS_NEVER].append(substance_name)
                        else:  # index FOUND
                            if substance.status not in substance_use:
                                substance_use[substance.status] = list()  # initialize
                            substance_use[substance.status].append(substance_name)
                            if len(specifics) == 1 and \
                                            substance.status in [Substance.STATUS_ACTIVE, Substance.STATUS_PREVIOUS]:
                                self.__scope.switchScopeTo(substance.name)  # ONLY 1 substance in query - store element

                    self.__response = ""  # clear/initialize

                    for key, value in substance_use.items():  # render response for each status type
                        if key == Substance.STATUS_ACTIVE:
                            self.__response += "I currently use {}. ".format(LUIS.joinWithAnd(value))
                        elif key == Substance.STATUS_PREVIOUS:
                            self.__response += "I used to use {}, but I quit. ".format(LUIS.joinWithAnd(value))
                        elif key == Substance.STATUS_NEVER:
                            pronoun, verb = self.modifyPronoun("have")
                            self.__response += "{} {} never used {}.".format(pronoun, verb,
                                                                             LUIS.joinWithAnd(value, joiner='or'))

        elif self.__scope.isScope(Scope.MEDICAL_HISTORY):  # PMH - treatment of a specific diagnosis
            disease = self.identifyObject("disease")
            if disease is not None:  # match found
                if disease.treatment:  # treatment was given
                    self.__response = ""  # initialize
                    if query_word == "how":
                        self.__response += "With "  # pre-pend 'with' to string
                    self.__response += LUIS.joinWithAnd(disease.treatment, prefix=True)
                else: self.__response = "It is not being treated"
            else:  # no match found
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "disease")
                self.__response = "Which diagnosis are you referring to?"

        elif self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_CURRENT) or \
                self.__scope.isScope(scope=Scope.CHIEF_COMPLAINT_PREVIOUS) or \
                        len(self.findMatchingEntity(of_type="symptom")) > 0:
            symptom = self.getSymptomForQuery()
            if symptom is not None:
                if len(self.findMatchingEntity("recognizerKeywords", ["doctor", "emergency department", "physician",
                                                                      "ed", "treatment", "treated"])) > 0:
                    # intent: did patient seek treatment (go to doctor) for symptom?
                    self.__response = "No"  # always respond 'no'
                else:  # question about medication usage for treatment
                    matches = list()  # list of medications used for the symptom
                    for medication in self.__patient.medications:
                        if medication.indication.strip().lower() == symptom.symptom:  # match
                            result = "didn't help"  # default result type
                            for factor in symptom.aggravating_factors:
                                if medication.name in factor:  # found medication name
                                    result = "made it worse"
                            for factor in symptom.alleviating_factors:
                                if medication.name in factor:  # found medication name
                                    result = "made it better"
                            matches.append("{} ({})".format(medication.name, result))  # add medication/result -> array
                    if len(matches) == 0:  # no matching medications
                        self.__response = "No, I haven't tried taking any medications."
                    else:  # at least 1 match
                        self.__response = "Yes - I tried {}.".format(LUIS.joinWithAnd(matches))

        elif self.__scope.isScope(Scope.BIRTH_HISTORY):  # Birth history - management of ectopic/miscarriage/abortion
            birth = self.identifyObject("birth")
            if birth is not None:
                if birth.management: self.__response = "With {}".format(LUIS.joinWithAnd(birth.management))  # OPTIONAL
            else:  # failure to ID - ask for clarification
                self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                          self.__entities, "timeQualifier")
                self.__response = "Which pregnancy are you referring to?"

    # --- <HPI> INTENT HANDLERS ---
    def handleGetModifiableIntent(self, query_word):  # "GET_MODIFIABLE_FACTORS" intent
        symptom = self.getSymptomForQuery()
        if symptom is not None:
            self.__response = ""  # clear existing text

            # (1) Check which entity was received (aggravating vs. alleviating):
            factors_by_keyword = dict()  # init empty dict
            if len(self.findMatchingEntity("recognizerKeywords", ["worse", "worsen", "aggravate"])) > 0:  # WORSE
                factors_by_keyword["worse"] = symptom.aggravating_factors
            if len(self.findMatchingEntity("recognizerKeywords", ["better", "improve", "alleviate"])) > 0:  # BETTER
                factors_by_keyword["better"] = symptom.alleviating_factors

            if len(factors_by_keyword.keys()) == 0:  # (2) there are NO keywords - generic 'change' query
                if len(symptom.aggravating_factors) > 0:
                    self.__response += "It gets worse " \
                                       "with {}. ".format(LUIS.joinWithAnd(symptom.aggravating_factors))
                else:  # no aggravating factors
                    self.__response += "Nothing makes it worse. "

                if len(symptom.alleviating_factors) > 0:
                    self.__response += "It is better " \
                                       "with {}. ".format(LUIS.joinWithAnd(symptom.alleviating_factors))
                else:  # no alleviating factors
                    self.__response += "Nothing makes it better."
                return  # terminate function

            # (3) Render the response for SPECIFIC modifiable factors:
            lead_word = False  # indicates if a lead word (Yes/No) has been placed yet, to avoid duplication
            for keyword, factors in factors_by_keyword.items():
                if len(factors) == 0:  # NO factors found
                    if ((query_word == "does") or (query_word == "did")) and not lead_word:  # YES/NO query
                        lead_word = True
                        self.__response += "No, "
                    self.__response += "nothing makes it {}. ".format(keyword)  # default
                elif len(factors) == 1:  # ONLY 1 factor found
                    if ((query_word == "does") or (query_word == "did")) and not lead_word:  # YES/NO query
                        self.__response += "Yes - "
                    self.__response += "{} makes it {}. ".format(factors[0], keyword)  # default
                else:  # > 1 factor found
                    if ((query_word == "does") or (query_word == "did")) and not lead_word:  # YES/NO query
                        self.__response += "Yes - "
                    self.__response += "{} all make it {}. ".format(LUIS.joinWithAnd(factors), keyword)  # default

    def handleGetPrecipitantIntent(self, query_word):  # "GET_PRECIPITANT" intent
        symptom = self.getSymptomForQuery()
        if symptom is not None:
            if symptom.precipitant is None:  # no precipitant
                self.__response = "Nothing"
            else:
                self.__response = symptom.precipitant

    def handleGetPreviousIntent(self, query_word):  # "GET_PREVIOUS" intent
        cc_count = self.__patient.getHPICount()  # check the number of CC elements to set the limit
        if (cc_count - 1) == 1:  # only 1 previous time - change scope
            self.__response = "Yes, 1 time before."
            self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_PREVIOUS)
        elif (cc_count - 1) > 1:  # more than 1 previous time - change scope
            self.__response = "Yes, {} times previously".format(cc_count - 1)
            self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_PREVIOUS)
        else:  # never previously experienced this complaint
            self.__response = "No, never."

    def handleGetProgressionIntent(self, query_word):  # "GET_PROGRESSION" intent
        symptom = self.getSymptomForQuery()
        if symptom is not None: self.__response = symptom.progression

    def handleGetSeverityIntent(self, query_word):  # "GET_SEVERITY" intent
        symptom = self.getSymptomForQuery()
        if symptom is not None:
            if (symptom.severity): self.__response = "A {} out of 10".format(symptom.severity)  # OPTIONAL property

    def handleGetSymptomsIntent(self, query_word):  # "GET_SYMPTOMS" intent
        # If only 1 symptom comes in - either an element switch OR an HPI query on the current open element
        # 2 symptoms - either a scope unwind (if 1 symptom is CC or top level element) or 2-symptom assoc. sx query

        # (1) Get the Symptom object for which to find associated symptoms:
        symptom = self.getSymptomForQuery(block_nesting=True)  # update scope but BLOCK nesting temporarily
        if symptom is None:  # NO symptom was found - default to CURRENT CC scope
            self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_CURRENT)  # reset scope -> CC
            symptom = self.__patient.symptoms[0]  # set object
        q_symptoms = self.processQuerySymptoms()  # get QUERY symptoms from handler
        cc = self.__patient.symptoms[0]  # set default -> CC_current object
        if self.__scope.isScope(Scope.CHIEF_COMPLAINT_PREVIOUS):  # scope = PREVIOUS
            cc = self.__patient.symptoms[1]  # overwrite w/ CC_previous object

        # (2) Construct the disambiguated ASSOCIATED symptoms list (from DB record) for the CURRENT symptom:
        assoc_symptoms = set(symptom.getAssociatedSymptoms())  # get UNIQUE D/A values for DB associated symptoms

        # (3) Construct the generic disambiguated ASSOCIATED symptoms list for the CC symptom:
        cc_assoc_symptoms = list()
        for s in cc.assoc_symptoms:  # add the assoc. symptoms -> list
            cc_assoc_symptoms.append(s.lower() if type(s) is str else s.symptom.lower())  # extract symptom names
        cc_da_list = list(self.__patient.disambiguate("symptom", cc.symptom))  # *init D/A list w/ CC.symptom*
        for da in self.__patient.disambiguate("symptom", symptom.symptom):  # add current symptom D/A -> list
            cc_da_list.append(da)
        for assoc in cc_assoc_symptoms:  # iterate through & disambiguate each ASSOCIATED symptom
            da = self.__patient.disambiguate("symptom", assoc)  # disambiguation
            for s in da: cc_da_list.append(s)  # add EACH disambiguation to the combined list
        cc_assoc_symptoms = set(cc_da_list)  # rewrite assoc_symptoms -> UNIQUE D/A values

        # (4) If there is a prepositional phrase, remove the symptom from the query list:
        preposition_symptom = self.findSymptomInPrepositionalPhrase()  # check for symptom in prepositional phrase
        if preposition_symptom is not None:  # if there is a prepositional symptom, delete it from query list
            for i, s in enumerate(q_symptoms):
                if s in self.__patient.disambiguate("symptom", preposition_symptom.entity):
                    del (q_symptoms[i])  # delete the prepositional symptom from query symptoms list

        if len(q_symptoms) == 0:  # generic request WITHOUT symptoms explicitly provided
            self.__patient.cacheQueryForClarification(self.__activity.getConversationID(), self.__topIntent,
                                                      self.__entities, "symptom")  # clarification logic
            self.__response = "Such as?"  # ask user to clarify
        else:  # at least 1 symptom in query
            assoc_matches = {"yes": list(), "no": list()}  # keeps track of matches for Symptom's associated symptoms
            cc_matches = {"yes": list(), "no": list()}  # keeps track of matches for CC associated symptoms
            for s in q_symptoms:  # for each query symptom, check if it is in assoc_symptoms
                if symptom.isQueryInAssociatedSymptoms(q_symptoms[0]):  # query IS in assoc. symptoms (from DB)
                    print("Checking in Symptom.assoc...")
                    if s in assoc_symptoms:  # match
                        assoc_matches["yes"].append(self.getCanonicalSymptom(symptom, s))  # add -> YES list
                        if len(q_symptoms) == 1:  # if there is ONLY 1 query, NEST the symptom in scope
                            self.nestSymptomInScope(q_symptoms[0])  # nest element - method AUTO-canonizes symptom
                    else:  # NOT a match
                        assoc_matches["no"].append(s)  # add -> NO list
                    self.__patient.updateMissedQuestionsForQuerySymptoms(symptom, [s])  # Feedback Module handler
                else:  # query is NOT in assoc. symptoms - check against CC's assoc_symptoms list
                    print("Checking in CC.assoc...")
                    if s in cc_assoc_symptoms:  # match
                        if s in self.__patient.disambiguate("symptom", symptom.symptom):  # query for CURRENT symptom
                            cc_matches["yes"].append(self.getCanonicalSymptom(symptom, s))  # add -> YES list
                        else:  # query for ANY OTHER symptom
                            cc_matches["yes"].append(self.getCanonicalSymptom(cc, s))  # add -> YES list

                        if len(q_symptoms) == 1 and \
                                (q_symptoms[0] not in self.__patient.disambiguate("symptom", cc.symptom) and
                                         q_symptoms[0] not in self.__patient.disambiguate("symptom", symptom.symptom)):
                            # There is ONLY 1 query symptom and it is NOT the chief complaint OR the current symptom
                            s_object = self.__patient.getSymptomObjectWithName(cc, q_symptoms[0], da=True)
                            if s_object is not None:  # query matches a <Symptom> object - nest in scope
                                print("Setting element [{}] in scope...".format(s_object.symptom))
                                self.__scope.switchScopeTo(Scope.ASSOC_SYMPTOMS)  # FIRST set subscope -> assoc. sx
                                self.__scope.switchScopeTo(s_object.symptom)  # set returned Symptom -> TOP lvl element
                    else:  # NOT a match
                        cc_matches["no"].append(s)  # add -> NO list
                    self.__patient.updateMissedQuestionsForQuerySymptoms(cc, [s])  # Feedback Module handler

            self.__response = ""  # initialize response
            pronoun, verb = self.modifyPronoun("have")  # get pronoun/verb pair
            if len(assoc_matches["yes"]) + len(assoc_matches["no"]) > 0:  # render responses IFF there is at least 1
                if len(assoc_matches["yes"]) == 0:  # no to ALL query symptoms
                    self.__response = "{} {} not had {} with the {}. ".format(pronoun, verb,
                                                                              LUIS.joinWithAnd(assoc_matches["no"],
                                                                                                joiner="or"),
                                                                              symptom.symptom)
                elif len(assoc_matches["no"]) == 0:  # yes to ALL query symptoms
                    self.__response = "{} {} had {} with the {}. ".format(pronoun, verb,
                                                                          LUIS.joinWithAnd(assoc_matches["yes"]),
                                                                          symptom.symptom)
                else:  # mixed response (yes to some, no to some)
                    self.__response = "{} {} had {}, but no {} with the {}. ".format(pronoun, verb,
                        LUIS.joinWithAnd(assoc_matches["yes"]),
                        LUIS.joinWithAnd(assoc_matches["no"], joiner="or"),
                        symptom.symptom)

            if len(cc_matches["yes"]) + len(cc_matches["no"]) > 0:  # render responses IFF there is at least 1
                if len(cc_matches["yes"]) == 0:  # no to ALL query symptoms
                    self.__response = "No {}".format(LUIS.joinWithAnd(cc_matches["no"], joiner="or"))
                elif len(cc_matches["no"]) == 0:  # yes to ALL query symptoms
                    self.__response = "{} {} had {}.".format(pronoun, verb, LUIS.joinWithAnd(cc_matches["yes"]))
                else:  # mixed response (yes to some, no to some)
                    self.__response = "{} {} had {}, but no {}.".format(pronoun, verb,
                                                                        LUIS.joinWithAnd(cc_matches["yes"]),
                                                                        LUIS.joinWithAnd(cc_matches["no"],joiner="or"))

    # --- HELPER FUNCTIONS ---
    def getCanonicalSymptom(self, current_symptom, query_symptom):  # returns CANONICAL form of the symptom name
        if query_symptom in self.__patient.disambiguate("symptom", current_symptom.symptom):  #(1) q_sx == CURRENT sx
            return current_symptom.symptom  # return symptom name

        for s in current_symptom.assoc_symptoms:  # (2) look for a match in the specified symptom's assoc array
            name = s if type(s) is str else s.symptom  # extract the symptom name
            if query_symptom in self.__patient.disambiguate("symptom", name):  # D/A each symptom in array INDIVIDUALLY
                return name

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

    def getSymptomForQuery(self, block_nesting=False):  # identifies Symptom object for ALL HPI-related intents
        # 'block_nesting': when set to True (by GetSymptoms intent), blocks the nesting of the current scope!
        print("\n[LUIS > getSymptomForQuery()] Examining query...")
        symptoms = self.processQuerySymptoms()  # set symptom entities using handler
        print("{} symptoms found - {}".format(len(symptoms), symptoms))

        if not (self.__scope.isScope(Scope.CHIEF_COMPLAINT_CURRENT) or
                self.__scope.isScope(Scope.CHIEF_COMPLAINT_PREVIOUS)):  # make sure SCOPE is set -> CC, else reset!
            self.__scope.switchScopeTo(new_scope=Scope.CHIEF_COMPLAINT_CURRENT)  # default -> CC current

        # (1) Check if a specific HPI element is being referenced in the query:
        cc_count = self.__patient.getHPICount()  # TOTAL number of CC elements
        if (cc_count - 1) > 0 and (
                        len(self.findMatchingEntity("timeQualifier", ["first", "earliest", "before",
                                                                      "last", "previous", "previously"])) > 0
                and self.nextWordAfter(entity_value="last", matches=["time", "episode", "instance"])
        ):  # the word 'last' can occur w/ 2 symptom contexts (other one is "how long does it last?") so do check!
            self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_PREVIOUS)  # reset PREVIOUS cc scope
        elif len(self.findMatchingEntity("timeQualifier", ["current", "currently", "next", "this time",
                                                           "most recent", "most recently"])) > 0:  # current
            self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_CURRENT)  # reset CURRENT cc scope

        # (2) Check if we need to UNWIND or NEST our scope based on the query symptom(s):
        if len(symptoms) == 1:  # only 1 query symptom
            if not symptoms[0] in self.__patient.disambiguate("symptom", self.__patient.chief_complaint):
                print("Symptom is NOT CC - checking if sx is OBJECT in assoc. array...")
                # if query has only 1 symptom, it is either to NEST an element or an HPI query on the CURRENT element
                # in EITHER case, our nestSymptom() function is safe to use!
                self.nestSymptomInScope(symptoms[0], block_nesting)  # nest scope element
            else:  # lone symptom is in CC D/A
                block_reset = False  # indicator to block reset
                current = self.__patient.findSymptomInScope(self.__scope)  # get current symptom in scope
                if current is not None:  # *if assoc. sx of CURRENT sx ALSO contains the query sx, do NOT reset!*
                    if current.isQueryInAssociatedSymptoms(symptoms[0]):  # query symptom is in assoc array
                        print("*** Symptom in CC D/A but is ALSO assoc sx of CURRENT sx!")
                        block_reset = True  # block reset
                    elif symptoms[0] in self.__patient.disambiguate("symptom", current.symptom):  # query = CURRENT sx
                        print("*** Symptom is in CC D/A but is ALSO in CURRENT sx D/A!")
                        block_reset = True

                if not block_reset:  # blocker was NOT set - remove any element or subscope
                    self.__scope.switchScopeTo(new_scope=(self.__scope.getScopeForDB()[0]))  # reset
        elif self.findSymptomInPrepositionalPhrase() is not None:  # check for prepositional phrase
            nearest_symptom = self.findSymptomInPrepositionalPhrase()

            # FIRST check if the nearest symptom is the CC or an EXISTING element in the scope:
            unwound = False  # indicator that unwinding occurred
            if nearest_symptom.entity in self.__patient.disambiguate("symptom", self.__patient.chief_complaint):
                print("Unwinding to chief complaint - ", end="")
                if self.__scope.isScope(Scope.CHIEF_COMPLAINT_CURRENT):  # scope = CURRENT
                    print("CURRENT")
                    self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_CURRENT)  # unwind -> CC current
                elif self.__scope.isScope(Scope.CHIEF_COMPLAINT_PREVIOUS):  # scope = PREVIOUS
                    print("PREVIOUS")
                    self.__scope.switchScopeTo(Scope.CHIEF_COMPLAINT_PREVIOUS)  # unwind -> CC previous
                unwound = True  # set indicator
            elif self.__scope.getElement(return_Full=True) is not None:  # check assoc. symptoms for unwind
                counter = 0  # maintains record of # of unwinds that are required
                for e in self.__scope.getElement(return_Full=True)[::-1]:  # traverse elements in REVERSE
                    counter += 1  # increment @ TOP of loop
                    if nearest_symptom.entity in self.__patient.disambiguate("symptom", e):  # D/A & check for match
                        print("Unwinding to ELEMENT [{}]".format(e))
                        for _ in range(counter):  # perform 1 unwind for EACH time loop was run
                            self.__scope.switchScopeTo(e, flag=0)  # unwind -> the matching element
                        unwound = True  # set indicator
                        break  # terminate loop after FIRST element match is found

            # LAST - if NO unwinding was performed, check if prepositional symptom is an assoc. sx of CURRENT symptom:
            if not unwound:  # 'nestSymptom()' performs ALL necessary checks, so simply call the function
                self.nestSymptomInScope(nearest_symptom.entity)  # if possible, nest symptom in scope

        return self.__patient.findSymptomInScope(self.__scope)  # (4) return the Symptom object using the open scope

    def findSymptomInPrepositionalPhrase(self):  # locates symptom in prepositional phrase (in theory)
        if len(self.findMatchingEntity(of_type="prepositionalPhrase")) > 0:  # check for prepositional phrase
            print("\nFound prepositional phrase...")
            preposition = self.findMatchingEntity(of_type="prepositionalPhrase", full=True)[0]
            nearest_symptom = None  # FIRST symptom AFTER the prepositional phrase
            q_symptoms = self.findMatchingEntity(of_type="symptom", full=True)  # all symptoms in query
            for s in q_symptoms:  # disambiguate, only want matching symptoms
                if s.startIndex > preposition.endIndex:  # make sure symptom's START is AFTER the prepositional phrase
                    if nearest_symptom is None:  # no cached symptom
                        nearest_symptom = s  # store s -> nearest symptom
                    elif s.startIndex < nearest_symptom.startIndex:  # THIS symptom is BEFORE the cached symptom
                        nearest_symptom = s  # store s -> nearest symptom
            return nearest_symptom
        return None

    def nestSymptomInScope(self, symptom_name, block=False):  # nests symptom in ELEMENT of current scope
        if not block:  # do not run routine if blocker is set
            print("NESTING symptom [{}] in scope...".format(symptom_name, block))
            s_object = self.__patient.findSymptomInScope(self.__scope, symptom_name)  # check for match in ASSOC.
            if s_object is not None:  # found Symptom object - nest scope
                print("Found Symptom object!")
                element = self.__scope.getElement(return_Full=True)  # access the FULL element
                if element is not None:  # safety check
                    if len(element) == 0:  # NO element is set yet...
                        print("No subscope yet - setting subscope...")
                        self.__scope.switchScopeTo(Scope.ASSOC_SYMPTOMS)  # FIRST set subscope
                else:  # element is NONE
                    print("No subscope yet - setting subscope...")
                    self.__scope.switchScopeTo(Scope.ASSOC_SYMPTOMS)  # FIRST set subscope
                self.__scope.switchScopeTo(s_object.symptom, flag=2)  # THEN nest (flag = 2) CANONICAL symptom -> elem.

    def processQuerySymptoms(self):  # obtains & processes QUERY symptoms using entities
        symptoms = set(self.findMatchingEntity("symptom"))  # get symptom entities as a SET
        tokenized = list()  # list of symptoms split into tokens
        for symptom in symptoms:  # split each symptom into individual tokens
            tokenized.append(symptom.split())

        for s in tokenized:  # remove LESS specific symptoms from list
            if len(s) == 1:  # single token symptom
                for tokens in tokenized:
                    if (s != tokens) and (s[0] in tokens):  # token is present in a DIFFERENT query symptom
                        symptoms.remove(s[0])  # delete symptom from query list
                        break  # terminate loop after removing
        return [s for s in symptoms]  # return values as LIST (not set)

    def identifyObject(self, category):  # sets the SCOPE based on the input category & query entities
        # ** analogous to HPI pass-through for NON-symptom objects **
        array = list()  # initialize list
        entity = category  # initialize entity keyword w/ category name
        if category == "disease":
            array = self.__patient.medical_history
        elif category == "surgery":
            array = self.__patient.surgical_history
        elif category == "medication":
            array = self.__patient.medications
        elif category == "allergy":
            array = self.__patient.allergies
        elif category == "family":
            array = self.__patient.family_history
            entity = "relationship"  # modify keyword to match entity name
        elif category == "substance":
            array = self.__patient.social_history.substances
        elif category == "travel":
            array = self.__patient.social_history.travel_history
            entity = "builtin.geography"  # use built-in 'geography' keyword to access specific elements
        elif category == "birth":
            if self.__patient.developmental_history is not None:  # Pediatrics patient
                return self.__patient.developmental_history.birth_history  # *RETURN Dev History > Birth Hx!*
            elif self.__patient.gynecologic_history is not None:  # non-Pediatrics patient
                array = self.__patient.gynecologic_history.birth_history  # use Gyn History > Birth Hx
            entity = "timeQualifier"  # use timeQualifier entity to ID which <Birth> obj is being referenced

        if len(array) == 1:  # only 1 element in array - no need to set scope b/c there's only 1 object
            return array[0]  # return 0th element
        else:  # > 1 element in array - check entities list to identify the element & set in scope
            keyword = self.findMatchingEntity(entity)  # check the specified entity
            if len(keyword) > 0:  # at least 1 keyword was found
                kwd = keyword[0]  # initialize keyword for object lookup
                if self.__is_clarification:  # clarification logic - needed to prevent looping
                    kwd = keyword[-1]  # *set the LAST entity -> keyword!*
                    # this overcomes issue where the current element (which caused a failed lookup) gets stuck in
                    # the scope b/c we keep replacing it w/ the 1st entity (guaranteed to be the SAME)

                obj = self.lookupObjectForScope(kwd)  # lookup object
                if obj is not None:  # object found - set object's identifier -> scope element
                    if category == "disease":
                        self.__scope.switchScopeTo(new_scope=obj.diagnosis.lower())
                    elif category == "surgery":
                        self.__scope.switchScopeTo(new_scope=obj.type.lower())
                    elif category == "medication":
                        self.__scope.switchScopeTo(new_scope=obj.name.lower())
                    elif category == "allergy":
                        self.__scope.switchScopeTo(new_scope=obj.allergen.lower())
                    elif category == "family":
                        self.__scope.switchScopeTo(new_scope=obj.relationship.lower())
                    elif category == "substance":
                        self.__scope.switchScopeTo(new_scope=obj.name.lower())
                    elif category == "travel":
                        self.__scope.switchScopeTo(new_scope=obj.location.lower())
                    elif category == "birth":
                        self.__scope.switchScopeTo(new_scope=kwd)  # use keyword in place of object property!
                    return obj

            return self.lookupObjectForScope()  # search for Object & return it if found

    def lookupObjectForScope(self, keyword=None):  # returns subscriptable Object based on the current SCOPE
        name = self.__scope.getElement() if keyword is None else keyword  # define lookup name
        if self.__scope.isScope(Scope.MEDICAL_HISTORY):  # generic disease lookup
            return self.__patient.getObject("disease", name)
        elif self.__scope.isScope(Scope.SURGICAL_HISTORY):  # generic surgery lookup
            return self.__patient.getObject("surgery", name)
        elif self.__scope.isScope(Scope.MEDICATIONS):  # generic medication lookup
            return self.__patient.getObject("medication", name)
        elif self.__scope.isScope(Scope.ALLERGIES):  # generic allergy lookup
            return self.__patient.getObject("allergy", name)
        elif self.__scope.isScope(Scope.FAMILY_HISTORY):  # generic family member lookup
            return self.__patient.getObject("family", name)
        elif self.__scope.isScope(Scope.SUBSTANCES):  # substance lookup
            return self.__patient.getObject("substance", name)
        elif self.__scope.isScope(Scope.TRAVEL_HISTORY):  # generic travel location lookup
            return self.__patient.getObject("travel", name)
        elif self.__scope.isScope(Scope.BIRTH_HISTORY):  # generic birth lookup
            return self.__patient.getObject("birth", name, scope=self.__scope)  # *input scope!*
        return None  # default if no match is found


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


# FEATURES:
# Features can be used to improve LUIS' ability to recognize intents & entities
# PHRASE LIST: contains some or all of an entity's potential values (similar to D/A); use for RARE words  |  LIMIT 10
# PATTERN FEATURES: uses regex to help LUIS recognize frequently observed patterns (e.g. flight numbers)  |  LIMIT 10

# TRAINING & TESTING:
# After creating your app's model (w/ intents, utterances, entities, & features), it's time to TRAIN & TEST
# Each time you edit the app  model, you MUST re-train the app before testing & publishing!
# When you train, LUIS generalizes from examples you have labeled, learning to recognize relevant intents/entities
# Note: LUIS provides a log of intents/entities from USERS that were passed -> the app; you can see how they were ID'd!
# After training, you can interactively test your app on a variety of queries to see how well it classifies inputs
# You can also do BATCH testing by submitting a JSON file w/ up to 1000 queries
# After publishing your app, you can compare the performance of a newly trained model to that of the published model

# ACTIVE LEARNING
# After your app is published & live, LUIS examines all of the user utterances it has received
# It calls to your attention specific utterances that it would like to have labeled b/c it is unsure
# Manually labeling these "SUGGESTED UTTERANCES" will significantly boost your performance