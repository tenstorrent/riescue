# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


class RawAttributes:
    """Dynamic attribute management with validation.

    This class allows runtime creation and modification of class attributes while enforcing
    a predefined set of valid attributes. It provides dynamic setter methods and validation
    on all attribute operations.

    :param valid_attrs: Dictionary of valid attributes and their default values
    :type valid_attrs: dict
    :param **kwargs: Initial values for attributes
    :type **kwargs: dict

    :raises ValueError: If attempting to set/get an invalid attribute

    Example:
        .. code-block:: python

            # Define valid attributes with defaults
            valid_attrs = {
                "name": "default",
                "age": 0,
                "active": False
            }

            # Create instance with some overrides
            user = RawAttributes(valid_attrs, name="John", age=25)

            # Use dynamic setters
            user.set_name("Jane")
            user.set_age(30)

            # Get values
            print(user.name)  # "Jane"
            print(user.age)   # 30

            # Invalid attribute raises error
            user.set_invalid("value")  # ValueError
    """

    valid_attrs = {}

    def __str__(self) -> str:
        result = "["
        for field in self.__dict__:
            result += f"{field}: {getattr(self, field)}"
        result += "]"
        return result

    def __init__(self, valid_attrs, **kwargs):
        # Initialize all attributes with default values
        RawAttributes.valid_attrs = valid_attrs
        for attr in valid_attrs:
            value = valid_attrs[attr]
            if attr in kwargs:
                value = kwargs.get(attr)
            setattr(self, attr, value)

        for attr in kwargs:
            # If attribute is not valid, assert
            if attr not in valid_attrs:
                raise ValueError(f"{self.__class__.__name__} class has no member {attr}")

        self.__dict__.update(kwargs)

    def get(self, attr):
        if attr not in RawAttributes.valid_attrs:
            raise ValueError(f"{self.__class__.__name__} class has no member {attr}")

        return self.__dict__[attr]

    def set(self, attr, value):
        if attr not in RawAttributes.valid_attrs:
            raise ValueError(f"{self.__class__.__name__} class has no member {attr}")

        self.__dict__[attr] = value

    def __getattr__(self, name):
        if name[0:4] == "set_":
            attr = name[4:]
            if attr not in RawAttributes.valid_attrs:
                raise ValueError(f"{self.__class__.__name__} class has no member {attr}")

            def setter(x):
                setattr(self, attr, x)
                return self

            return setter
        elif name[0:4] == "get_":
            raise ValueError(f"get() method is not supported in class " f"{self.__class__.__name__}")
        else:
            return super(RawAttributes, self).__getattribute__(name)
