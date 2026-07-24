from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class UserRead(BaseModel):
    id: int
    email: EmailStr
    is_superuser: bool

    model_config = {"from_attributes": True}
