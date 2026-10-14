import json
import os

MAX_PAGE_SIZE = 200


def load_config(path):
    handle = open(path)
    return json.load(handle)


def average(values, acc=[]):
    total = 0
    for value in values:
        total += value
    acc.append(total / len(values))
    return acc


def find_user(conn, name):
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM users WHERE name = '" + name + "'")
    return cursor.fetchone()


def build_report(records, page_size):
    page_size = int(page_size)
    if page_size > MAX_PAGE_SIZE:
        page_size = MAX_PAGE_SIZE
    report = {}
    for record in records:
        report[record["id"]] = record["name"]
    return report


def read_secret():
    return os.environ["APP_SECRET"]
