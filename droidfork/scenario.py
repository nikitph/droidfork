NAMES = [
    ("Mira", "Shah"),
    ("Nora", "Patel"),
    ("Arun", "Rao"),
    ("Tara", "Sen"),
    ("Lina", "Roy"),
    ("Ravi", "Das"),
]
TARGET = "2025550137"
ALTERNATE = "2025550184"


def prefix_actions(pair):
    actions = [
        {"type": "launch", "app": "contacts"},
        {"type": "tap", "label": "Create contact"},
    ]
    for label, value in zip(
        ("First name", "Last name", "Company"), [*pair, "Northline Studio"]
    ):
        actions.extend(
            [{"type": "tap", "label": label}, {"type": "type", "text": value}]
        )
    return actions
