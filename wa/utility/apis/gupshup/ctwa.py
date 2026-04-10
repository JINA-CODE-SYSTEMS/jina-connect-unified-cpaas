from .base_api import WAAPI


class CTWAAPI(WAAPI):
    """
    CTWA API class to handle CTWA related operations.
    1. Create New Service
    2. Retrieve Service
    """
    BASE_URL:str = "https://partner-ctx.gupshup.io/api/v2"
    api_key: str


    

    @property
    def _create_new_service(self):
        return f"{self.BASE_URL}/services/"
    
    @property
    def _retrieve_service(self):
        return f"{self.BASE_URL}/services/{{serviceId}}"

    @property
    def _perform_action(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/actions"
    
    @property
    def _create_goal(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/goals"
    
    @property
    def _retrieve_goals(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/goals"
    
    @property
    def _update_goal(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/goals/{{goalId}}/actions"
    
    @property
    def _get_goal_details(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/goals/{{goalId}}"
    
    @property
    def _get_goal_versions(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/goals/{{goalId}}/versions"
    
    @property
    def _get_goal_version_details(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/goals/{{goalId}}/versions/{{versionId}}"
    
    @property
    def _mark_lead_ad_milestone_achieved(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/goals/{{goalId}}/milestones/{{milestoneIndex}}"
    
    @property
    def _get_lead_details(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/leads/{{leadId}}"
    
    @property
    def _get_lead_associated(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/leads"
    
    @property
    def _update_lead_retargeting_schedule(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/lead-retargeting-schedule"

    @property
    def _create_retargeting_schedule(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/retargeting-schedules"
    
    @property
    def _get_retargeting_schedules(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/retargeting-schedules"

    @property
    def _get_retargeting_schedule(self):
        return f"{self.BASE_URL}/services/{{serviceId}}/retargeting-schedules/{{scheduleId}}"

    def create_new_service(self, data: dict):
        url = self._create_new_service
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def retrieve_service(self, serviceId: str):
        url = self._retrieve_service.format(serviceId=serviceId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def perform_action(self, serviceId: str, data: dict):
        url = self._perform_action.format(serviceId=serviceId)
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def create_goal(self, serviceId: str, data: dict):
        url = self._create_goal.format(serviceId=serviceId)
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def retrieve_goals(self, serviceId: str):
        url = self._retrieve_goals.format(serviceId=serviceId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def update_goal(self, serviceId: str, goalId: str, data: dict):
        url = self._update_goal.format(serviceId=serviceId, goalId=goalId)
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def get_goal_details(self, serviceId: str, goalId: str):
        url = self._get_goal_details.format(serviceId=serviceId, goalId=goalId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def get_goal_versions(self, serviceId: str, goalId: str):
        url = self._get_goal_versions.format(serviceId=serviceId, goalId=goalId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)

    def get_goal_version_details(self, serviceId: str, goalId: str, versionId: str):
        url = self._get_goal_version_details.format(serviceId=serviceId, goalId=goalId, versionId=versionId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def mark_lead_ad_milestone_achieved(self, serviceId: str, goalId: str, milestoneIndex: str, data: dict):
        """
        Mark a lead ad milestone as achieved.
        
        Args:
            serviceId (str): The service ID
            goalId (str): The goal ID
            milestoneIndex (str): The milestone index
            data (dict): Request payload data
            
        Returns:
            dict: API response
        """
        url = self._mark_lead_ad_milestone_achieved.format(
            serviceId=serviceId, 
            goalId=goalId, 
            milestoneIndex=milestoneIndex
        )
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def get_lead_details(self, serviceId: str, leadId: str):
        """
        Get details of a specific lead.
        
        Args:
            serviceId (str): The service ID
            leadId (str): The lead ID
            
        Returns:
            dict: API response containing lead details
        """
        url = self._get_lead_details.format(serviceId=serviceId, leadId=leadId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def get_lead_associated(self, serviceId: str, params: dict = None):
        """
        Get all leads associated with a service.
        
        Args:
            serviceId (str): The service ID
            params (dict, optional): Query parameters for filtering/pagination
            
        Returns:
            dict: API response containing leads list
        """
        url = self._get_lead_associated.format(serviceId=serviceId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers,
            "params": params or {}
        }
        return self.make_request(request_data)
    
    def update_lead_retargeting_schedule(self, serviceId: str, data: dict):
        """
        Update lead retargeting schedule for a service.
        
        Args:
            serviceId (str): The service ID
            data (dict): Retargeting schedule data
            
        Returns:
            dict: API response
        """
        url = self._update_lead_retargeting_schedule.format(serviceId=serviceId)
        request_data = {
            "method": "PUT",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def create_retargeting_schedule(self, serviceId: str, data: dict):
        """
        Create a new retargeting schedule for a service.
        
        Args:
            serviceId (str): The service ID
            data (dict): Retargeting schedule configuration
            
        Returns:
            dict: API response
        """
        url = self._create_retargeting_schedule.format(serviceId=serviceId)
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def get_retargeting_schedules(self, serviceId: str, params: dict = None):
        """
        Get all retargeting schedules for a service.
        
        Args:
            serviceId (str): The service ID
            params (dict, optional): Query parameters for filtering
            
        Returns:
            dict: API response containing retargeting schedules
        """
        url = self._get_retargeting_schedules.format(serviceId=serviceId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers,
            "params": params or {}
        }
        return self.make_request(request_data)
    
    def get_retargeting_schedule(self, serviceId: str, scheduleId: str):
        """
        Get details of a specific retargeting schedule.
        
        Args:
            serviceId (str): The service ID
            scheduleId (str): The retargeting schedule ID
            
        Returns:
            dict: API response containing schedule details
        """
        url = self._get_retargeting_schedule.format(serviceId=serviceId, scheduleId=scheduleId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)

