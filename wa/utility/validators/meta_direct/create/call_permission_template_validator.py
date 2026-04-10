from typing import Any, List, Literal, Optional, Union

from wa.utility.data_model.meta_direct.body import BodyComponent
from wa.utility.data_model.meta_direct.buttons import CallPermissionButton

from .base_validator import BaseTemplateValidator


class CallPermissionRequestMessageValidator(BaseTemplateValidator):
    """
    Base class for template validators.
    """
    components: List[Union[BodyComponent,CallPermissionButton]]

    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)
        

