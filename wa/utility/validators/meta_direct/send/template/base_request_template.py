from typing import List, Literal, Optional, Union

from pydantic import BaseModel
from wa.utility.data_model.meta_direct.body import BodyComponent
from wa.utility.data_model.meta_direct.buttons_component import \
    ButtonsComponent
from wa.utility.data_model.meta_direct.footer import FooterComponent
from wa.utility.data_model.meta_direct.header import HeaderComponent


class LangaugeInput(BaseModel):
    """
    Language input for template request.
    """
    code: str

class ComponentInput(BaseModel):
    """
    Component input for template request.
    """
    type: str
    parameters: Optional[List[dict]] = None


class TemplateRequestBodyValidator(BaseModel):
    """
    Base class for template validators.
    """
    name: str
    language: LangaugeInput
    components: Optional[List[ComponentInput]] = None


class BaseRequestTemplateValidator(BaseModel):
    """
    Base class for template validators.
    """
    message_product: Literal["whatsapp"] = "whatsapp"
    recipient_type: Literal["individual"] = "individual"
    to: str
    type: Literal["template"] = "template"
    template: TemplateRequestBodyValidator

    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)
        

