from urllib.parse import parse_qs
from datetime import datetime
from flask import Request


class FilterServerSide:
    def __init__(self, request:Request, list:list,):
       

        # Define a list of accepted values for the parameterValueType
        accepted_values = ["bool", "string", "date", "number"]

        # Check if the provided parameterValueType is in the list of accepted values
        #if parameterValueType not in accepted_values:
                # If it's not, raise a ValueError with a custom message
            #raise ValueError(f"Invalid value for parameterValueType. Accepted values are {accepted_values}")
        
        self.request = request
        self.list = list
        #self.parameterKey = parameterKey
        #self.parameterValue = parameterValue
        #self.parameterValueType = parameterValueType



       

        self.parameterKeys= self.GetParameterKeysFromRequestForQueryKeyes()
        self.jsonKeys = self.GetParameterKeysFromRequestForJosnKeys()

        for parameterKey in self.parameterKeys:
            print('parameterKey:',parameterKey)
            self.list =self.MyFilter(parameterKey)





    def MyFilter(self,parameterKey):
        """
        This method is responsible for filtering the list based on the values extracted from the query string.
        It calls the GetFilterValues method to get the values, and then filters the list based on the values.
        """

      
        # get the filter type from the query string
        type = self.GetParameterValuesTypeFromRequest(parameterKey)
        print('type:',type)

        # get the filter operator from the query string
        operator = self.GetParameterOperatorFromRequest(parameterKey)
        print('operator:',operator)


          # Get the filter values from the query string
        values  = self.GetFilterValues(parameterKey,type)
        print('values:',values)


       
      
        print('values1:',len(self.list))
        # If the values are not None and the length is greater than 0, filter the list based on the values
        if values is not None and len(values) > 0 and type == "string":
            print('is string')
            self.list = list(filter(lambda row: str(self.get_nested_value(row, parameterKey)) in values, self.list))
           
        
        # If the values are not None and the length is greater than 0, filter the list based on the values
        if values is not None and len(values) > 0 and type == "date":
            print('is date')
            self.list = list(filter(lambda row: self.get_nested_value(row, parameterKey) in values, self.list))

        # If the values are not None and the length is greater than 0, filter the list based on the values 
        if values is not None and len(values) > 0 and type == "bool":
            print('is bool')
            self.list = list(filter(lambda row: self.get_nested_value(row, parameterKey) in values, self.list))


        # If the values are not None and the length is greater than 0, filter the list based on the values
        # type is number
        if values is not None and len(values) > 0 and type == "number":
            print('is number')
            self.list = list(filter(lambda row: values[0] <= self.get_nested_value(row, parameterKey) and self.get_nested_value(row, parameterKey) <= values[1], self.list))
        
        print('values2:',len(self.list))

        return self.list
           
    

   
    def GetFilterValues(self,parameterKey,type):
        """
        This method is responsible for extracting the filter values from the query string of the request.
        It decodes the query string, parses it into a dictionary, and retrieves the values for the specified parameter key.
        Depending on the specified parameter value type, it returns the values as is, or converts them to date or boolean objects.
        """


        query_string = self.request.query_string.decode()
        parsed_query = parse_qs(query_string)
        values = parsed_query.get(parameterKey, [])


        # If the parameter value type is a string, return the values as is
        if type == "string":
            return values
        
        # If the parameter value type is a date, convert the values to date objects
        if type == "date":
             date_values = []
             for value in values:
                 date_values.append(datetime.strptime(value, '%Y-%m-%d').date())
             return date_values
        
        #If the parameter value type is a boolean, convert the values to boolean objects
        if type == "bool":
            bool_values = []
            for value in values:
                if value.lower() == 'true':
                    bool_values.append(True)
                elif value.lower() == 'false':
                    bool_values.append(False)
            return bool_values
        
        #If the parameter value type is a number, convert the values to number objects
        if type == "number":
            number_values = []
            for value in values:
                number_values.append(int(value))
            return number_values


   
    def get_nested_value(self,row,parameterKey):
        """
        This method is responsible for getting the value of a nested key from a dictionary.
        It takes a dictionary and a key as input, and returns the value of the nested key.
        If the key is not found, it returns None.
        """
         
        #replace the _Qkey with _Jkey in the parameterKey
        if parameterKey.endswith('_Qkey'):
             parameterValue = parameterKey.replace('_Qkey','_Jkey')

        if parameterKey.endswith('_Qkey[]'):
             parameterValue = parameterKey.replace('_Qkey[]','_Jkey')

        keys = self.GetParameterValuesFromRequest(parameterValue)
        print('keys:',keys)

        
        keys = keys.split('.')
        value = row
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value



    def GetParameterKeysFromRequestForQueryKeyes(self):
        """
        This method is responsible for getting the parameter keys from the request.
        It extracts the keys from the query string and returns them as a list.
        """
       
       
        keys = [key for key in self.request.args.to_dict().keys() if key.endswith('_Qkey') or key.endswith('_Qkey[]')]


       
        return keys
    

       
    def GetParameterKeysFromRequestForJosnKeys(self):
        """
        This method is responsible for getting the parameter keys from the request.
        It extracts the keys from the query string and returns them as a list.
        """

        keys = [key for key in self.request.args.to_dict().keys() if key.endswith('_Jkey')]


        return keys
    
    
    def GetParameterValuesFromRequest(self,parameterKey):
        """
        This method is responsible for getting the parameter values from the request.
        It extracts the values from the query string and returns them as a list.
        """
        values = self.request.args.get(parameterKey)
        return values
    

    
    def GetParameterValuesTypeFromRequest(self,parameterKey):
        """
        This method is responsible for getting the parameter values from the request.
        It extracts the values from the query string and returns them as a list.
        """

        #replace the _Qkey with _Jkey in the parameterKey
        if parameterKey.endswith('_Qkey'):
             parameterType = parameterKey.replace('_Qkey','_Type')

        if parameterKey.endswith('_Qkey[]'):
             parameterType = parameterKey.replace('_Qkey[]','_Type')

        values = self.request.args.get(parameterType)
        return values
    

    def GetParameterOperatorFromRequest(self,parameterKey):
        """
        This method is responsible for getting the parameter values from the request.
        It extracts the values from the query string and returns them as a list.
        """

         #replace the _Qkey with _Jkey in the parameterKey
        if parameterKey.endswith('_Qkey'):
             parameterOperator = parameterKey.replace('_Qkey','_Operator')

        if parameterKey.endswith('_Qkey[]'):
             parameterOperator = parameterKey.replace('_Qkey[]','_Operator')


        values = self.request.args.get(parameterOperator)
        return values
