import json


def load_config(path):
    with open(path) as handle:
        return json.load(handle)


def average(values):
    total = 0
    for value in values:
        total += value
    return total / len(values)


def find_user(conn, name):
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM users WHERE active = 1")
    rows = cursor.fetchall()
    for row in rows:
        if row[1] == name:
            return row
    return None
