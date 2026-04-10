"""
Header Component for META Direct API Template Validation
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from .enums import HeaderFormat


class HeaderTextExample(BaseModel):
    """Example for text header with parameters"""
    header_text: List[str] = Field(
        ..., description="Example values for header text parameters"
    )


class HeaderHandleExample(BaseModel):
    """Example for media header with handle"""
    header_handle: List[str] = Field(..., description="Media handle IDs for header")


class HeaderComponent(BaseModel):
    """Header component - optional"""
    type: Literal["header"] = "header"
    format: HeaderFormat = Field(..., description="Header format type")
    text: Optional[str] = Field(
        None, description="Header text (required for TEXT format)"
    )
    example: Optional[Union[HeaderTextExample, HeaderHandleExample]] = Field(
        None, description="Example data for header"
    )

    @model_validator(mode="after")
    def validate_header(self):
        if self.format == HeaderFormat.TEXT:
            if not self.text:
                raise ValueError("Text is required for TEXT header format")
            # Check if text has parameters and requires examples
            if re.search(r"\{\{[^}]+\}\}", self.text) and not self.example:
                raise ValueError(
                    "Example is required when header text contains parameters"
                )
        elif self.format in [
            HeaderFormat.IMAGE,
            HeaderFormat.VIDEO,
            HeaderFormat.DOCUMENT,
        ]:
            if not self.example or not isinstance(self.example, HeaderHandleExample):
                raise ValueError(
                    f"header_handle example is required for {self.format.value} header format"
                )
        return self
