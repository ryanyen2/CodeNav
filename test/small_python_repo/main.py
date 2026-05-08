from utils import add, concat


def greet(name: str) -> str:
    from datetime import date
    name = name.strip() if isinstance(name, str) else str(name)
    return f"Hello, {name}! This year is {date.today().year}."


def run() -> None:
    print(greet("world"))
    print(add(1, 2))


if __name__ == "__main__":
    run()
