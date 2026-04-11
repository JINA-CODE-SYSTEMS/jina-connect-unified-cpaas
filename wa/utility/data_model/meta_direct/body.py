"""
Body Component for META Direct API Template Validation
"""

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class BodyTextNamedParam(BaseModel):
    """Named parameter for body text"""

    param_name: str = Field(..., description="Parameter name (without curly braces)")
    example: str = Field(..., description="Example value for the parameter")

    @field_validator("param_name")
    @classmethod
    def validate_param_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Parameter name cannot be empty")
        # Parameter names should be alphanumeric with underscores
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError(
                "Parameter name must start with letter/underscore and contain only alphanumeric/underscores"
            )
        return v.strip()


class BodyTextExample(BaseModel):
    """Example for body text with named parameters"""

    body_text_named_params: Optional[List[BodyTextNamedParam]] = Field(
        None, description="Named parameters with examples"
    )
    body_text: Optional[List[List[str]]] = Field(None, description="Positional parameter examples")


class BodyComponent(BaseModel):
    """Body component - required"""

    type: Literal["body"] = "body"
    text: str = Field(..., min_length=1, max_length=1024, description="Body text content")
    example: Optional[BodyTextExample] = Field(None, description="Example data for body parameters")

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Body text cannot be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_body_parameters(self):
        # Find all parameters in text (both {{param_name}} and {{1}}, {{2}} formats)
        named_params = re.findall(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}", self.text)
        positional_params = re.findall(r"\{\{(\d+)\}\}", self.text)

        has_params = len(named_params) > 0 or len(positional_params) > 0

        if has_params and not self.example:
            raise ValueError("Example is required when body text contains parameters")

        if has_params and self.example:
            # Validate named parameters
            if named_params:
                if not self.example.body_text_named_params:
                    raise ValueError(f"body_text_named_params is required for named parameters: {named_params}")
                provided_params = {p.param_name for p in self.example.body_text_named_params}
                missing_params = set(named_params) - provided_params
                if missing_params:
                    raise ValueError(f"Missing example values for parameters: {missing_params}")

            # Validate positional parameters
            if positional_params:
                if not self.example.body_text:
                    raise ValueError("body_text is required for positional parameters")

        return self
