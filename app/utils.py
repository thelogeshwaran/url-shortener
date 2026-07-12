import random
import string

CODE_LENGTH = 6


def generate_code() -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=CODE_LENGTH))
