import datetime

def datetime_from_isoformat(s):
    # Thanks SO! (https://stackoverflow.com/questions/30999230/how-to-parse-timezone-with-colon)
    if ":" == s[-3:-2]:
        s = s[:-3]+s[-2:]
    return datetime.datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z')
