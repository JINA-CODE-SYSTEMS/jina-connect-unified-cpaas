from pydantic import BaseModel


class CreateApp(BaseModel):
    name: str
    templateMessaging: bool = True
    disableOptinPrefUrl: bool = False
