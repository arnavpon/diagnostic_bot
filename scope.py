# defines all scope-related logic

class Scope:

    # --- CATEGORIES ---
    SCOPE = -1  # highest level in hierarchy
    SUBSCOPE = -2
    ELEMENT = -3  # lowest level in hierarchy

    # --- SCOPES ---
    CHIEF_COMPLAINT_CURRENT = 0  # CURRENT instance of chief complaint
    CHIEF_COMPLAINT_PREVIOUS = 1  # PREVIOUS instances of chief complaint
    MEDICAL_HISTORY = 2
    SURGICAL_HISTORY = 3
    MEDICATIONS = 4
    ALLERGIES = 5
    FAMILY_HISTORY = 6
    SOCIAL_HISTORY = 7
    SUBSTANCES = 8  # treat as a scope (NOT sub-scope) b/c user can ask about substance use outside of SH
    TRAVEL_HISTORY = 9  # treat as a scope (NOT sub-scope) b/c user can ask about travel of SH
    SEXUAL_HISTORY = 10  # treat as a scope (NOT sub-scope) b/c user can ask about sexual history outside of SH
    GYNECOLOGIC_HISTORY = 11  # special history (for OBGYN patients)
    BIRTH_HISTORY = 12  # special history (for Pediatric & OBGYN patients)

    # --- SUB-SCOPES ---
    ASSOC_SYMPTOMS = 15  # {< Chief Complaint}

    # --- INSTANCE METHODS ---
    def __init__(self, stored_scope=None):  # init w/ tuple of length 3 (scope, subscope, element)
        # scope: <Int> (rawValue) | sub-scope: <Int> (rawValue) | element: <Str> matched to element name
        if stored_scope is not None:  # tuple was provided from DB record
            assert (type(stored_scope) is tuple or type(stored_scope) is list) and \
                   len(stored_scope) == 3  # make sure the input tuple/list has 3 entries
            self.__scope, self.__subscope, self.__element = stored_scope  # break down elements
        else:  # NO stored_scope was provided - initialize w/ CURRENT CC scope
            self.__scope, self.__subscope, self.__element = Scope.CHIEF_COMPLAINT_CURRENT, None, None
        print("\nInitialized scope: [S] {} | [SS] {} | [E] {}".format(self.__scope, self.__subscope, self.__element))

    def getScopeCategory(self, scope):  # categorizes scope as a 'scope', 'sub-scope', or 'element'
        if type(scope) is str:  # string entry MUST be an ELEMENT
            return Scope.ELEMENT
        else:  # Int entries are scopes or sub-scopes
            if (scope >= 0) and (scope < 15):  # scope values lie between 0 & 15
                return Scope.SCOPE
            elif scope >= 15:  # SUB-scope values are greater than or equal to 15
                return Scope.SUBSCOPE

    def switchScopeTo(self, new_scope, flag=None):  # modifies the current scope object
        # 'flag': for SYMPTOM elements ONLY - 0 = topLvl, 1 = sameLvl, 2 = lowerLvl
        assert type(new_scope) is str or type(new_scope) is int  # type check
        category = self.getScopeCategory(scope=new_scope)  # classify the new scope into a category
        if category == Scope.SCOPE:
            self.__scope = new_scope
            self.__subscope = None  # erase any existing subscope
            self.__element = None  # erase any existing element
        elif category == Scope.SUBSCOPE:
            assert self.__scope is not None  # make sure a scope is set BEFORE a sub-scope can be set
            self.__subscope = new_scope
            self.__element = None  # erase the lower level category
        elif category == Scope.ELEMENT:
            assert self.__scope is not None  # make sure a scope is set BEFORE an element can be set
            if (flag is None) or (self.__element is None):  # default behavior
                self.__element = [new_scope]  # set the element as a LIST
            elif flag == 0:  # TOP level flag for SYMPTOM element - UNWIND 1 lvl
                if len(self.__element) > 1:  # make sure there are at least 2 elements before deleting the last one
                    self.__element.__delitem__(-1)  # remove the existing LAST element
                self.__element[-1] = new_scope  # update the NEW last element w/ the input symptom
            elif flag == 1:  # SAME level flag for SYMPTOM element - change element on CURRENT level
                self.__element[-1] = new_scope
            elif (flag == 2) and (new_scope != self.__element[-1]):  # LOWER level flag for SYMPTOM element
                # Make sure the new element is NOT the same as the current element, then NEST 1 level deeper:
                self.__element.append(new_scope)
        print("[switchScope] Scope = [{}]. Subscope = [{}]. Element = {}.".format(self.__scope,
                                                                                    self.__subscope,
                                                                                    self.__element))

    def isScope(self, scope=None, subscope=None, element=None):  # checks if OPEN scope == INPUT scope
        if element is not None:  # an element was input
            if self.__subscope is not None:  # sub-scope IS defined in self - match to scope + sub-scope + element
                if (self.__scope == scope) and \
                        (self.__subscope == subscope) and \
                        (self.__element == element): return True
            else:  # sub-scope is NOT defined in self - match ONLY to scope + element
                if (self.__scope == scope) and (self.__element == element): return True
        elif subscope is not None:  # a sub-scope was input - check that scope AND sub-scope match
            if (self.__scope == scope) and (self.__subscope == subscope): return True
        elif scope is not None:  # ONLY a scope was input
            if self.__scope == scope: return True  # only the scopes need to match
        return False  # default return value

    def getElement(self, return_Full=False, flag=None):  # returns the current ELEMENT or some subset of it
        # 'flag': for SYMPTOM elements only - 0 == TOP level element (if it exists), 1 == CURRENT level element
        if return_Full:  # indicator set - return the FULL array w/o subscripting
            return self.__element
        else:  # indicator set to False
            if self.__element is not None:
                if flag == 0:  # TOP level flag - return the element that is 1 level up
                    if len(self.__element) > 1:  # make sure there are AT LEAST 2 elements (else top lvl = current lvl)
                        return self.__element[-2]  # return SECOND-TO-LAST element
                    # *nest the length condition INSIDE the flag condition so None is returned if len <= 1*
                elif flag == 1:  # CURRENT level flag
                    return self.__element[-1]  # return LAST element
                else:  # NO flag set - return FIRST element in array
                    return self.__element[0]  # subscript the list
        return None

    def getScopeForDB(self):  # returns the current scope's values to persist in DB
        return (self.__scope, self.__subscope, self.__element)