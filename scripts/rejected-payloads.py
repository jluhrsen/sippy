#!/usr/bin/env python
# Example usage:
#   ./scripts/rejected-payloads.py --days 5 -d $POSTGRES_DSN categorize -r 4.14 -a amd64

import argparse
import datetime
from sqlalchemy import create_engine
from sqlalchemy import ARRAY, Column, DateTime, String
from sqlalchemy.orm import sessionmaker, declarative_base

base = declarative_base()

class ReleaseTags(base):
    __tablename__ = 'release_tags'

    id = Column(String, primary_key=True)
    release_tag = Column(String)
    release = Column(String)
    release_time = Column(DateTime)
    architecture = Column(String)
    stream = Column(String)
    phase = Column(String)
    reject_reason = Column(String)
    reject_reason_note = Column(String)
    reject_reasons = Column(ARRAY(String))

class PayloadTestFailures(base):
    __tablename__ = 'payload_test_failures_14d_matview'

    id = Column(String, primary_key=True)
    release_tag = Column(String)
    name = Column(String)
    prow_job_name = Column(DateTime)

def selectReleases(session, release, stream, architecture, showAll, days):
    selectedTags = []
    start = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    releaseTags = session.query(ReleaseTags).filter(ReleaseTags.phase == "Rejected", ReleaseTags.release_time >= start).order_by(ReleaseTags.release_time.desc()).all()
    for releaseTag in releaseTags:
        if release and releaseTag.release != release:
            continue
        if stream and releaseTag.stream != stream:
            continue
        if not showAll and releaseTag.reject_reason:
            continue
        if architecture and releaseTag.architecture != architecture:
            continue
        selectedTags.append(releaseTag)

    return selectedTags

def printReleases(selectedTags):
    print("%-10s%-50s%-20s%-20s%s" % ("index", "release tag", "phase", "reject reasons", "note"))
    for idx, releaseTag in enumerate(selectedTags):
        if releaseTag.reject_reasons is not None:
            reject_reasons_lines = "\n".join(releaseTag.reject_reasons).split("\n")
        else:
            reject_reasons_lines = [releaseTag.reject_reason]
        if len(reject_reasons_lines) > 1:
            # Multiple reasons get treated differently so the reasons are stack and the output looks pleasant.
            print("%-10d%-50s%-20s%-20s%s" % (idx+1, releaseTag.release_tag, releaseTag.phase, reject_reasons_lines[0], releaseTag.reject_reason_note))
            for reason_line in reject_reasons_lines[1:]:
                print("%-10s%-50s%-20s%-20s" % ("", "", "", reason_line))
        else:
            print("%-10d%-50s%-20s%-20s%s" % (idx+1, releaseTag.release_tag, releaseTag.phase, reject_reasons_lines[0], releaseTag.reject_reason_note))

def list_releases(session, release, stream, architecture, showAll, days):
    selectedTags = selectReleases(session, release, stream, architecture, showAll, days)
    printReleases(selectedTags)

reject_reasons = {
        "TEST_FLAKE": "tests intermittently failed and then corrected",
        "CLOUD_INFRA": "inability to obtain cloud infrastructure or outages",
        "CLOUD_QUOTA": "lack of quota on our CI accounts or rate limiting",
        "RH_INFRA": "outage/problem in OpenShift CI or Red Hat registries",
        "PRODUCT_REGRESSION": "actual product regression that needs a fix",
        "TEST_REGRESSION": "regression in the test framework",
}

max_test_failures_printed_per_job = 5

def categorizeSingle(session, tag):
    releaseTags = session.query(ReleaseTags).filter(ReleaseTags.release_tag == tag).all()
    reject_reasons_keys = list(reject_reasons.keys())
    for releaseTag in releaseTags:

        # Lookup and display test failures for this payload. If excessive numbers, limit to just a few.
        test_failures = session.query(PayloadTestFailures).filter(PayloadTestFailures.release_tag == tag).all()
        print()
        print("Blocking job test failures in payload: %s" % tag)
        print()
        job_to_test_failures = {}
        for test_failure in test_failures:
            if test_failure.prow_job_name not in job_to_test_failures:
                job_to_test_failures[test_failure.prow_job_name] = []
            job_to_test_failures[test_failure.prow_job_name].append(test_failure.name)
        for job in job_to_test_failures:
            print("%s:" % job)
            # print max 5 and indicate if there were more:
            for test_name in job_to_test_failures[job][:max_test_failures_printed_per_job]:
                print("   %s" % test_name)
            if len(job_to_test_failures[job]) > max_test_failures_printed_per_job:
                print("  ... and %d more" % (len(job_to_test_failures[job])-max_test_failures_printed_per_job))

        print()
        print("Please choose the reject reason for tag %s from the following list:" % releaseTag.release_tag)
        for idx, reason in enumerate(reject_reasons_keys):
            print("%10d: %20s - %s" % (idx+1, reason, reject_reasons[reason]))

        while True:
            val = input("Enter one or more selections between 1 and " + str(len(reject_reasons_keys)) + " separated by spaces: ")
            try:
                # Values outside the legal range are ignored.  Invalid values throw an exception so you can
                # try again.
                selected_indexes = [i for i in map(int, val.split()) if 1 <= i <= len(reject_reasons_keys)]
                if selected_indexes:
                    break
            except ValueError:
                print("  This contains invalid integer values: %s" % val)
                print("  Please try again")
                continue
        releaseTag.reject_reason = reject_reasons_keys[selected_indexes[0]-1]
        releaseTag.reject_reasons = [reject_reasons_keys[i-1] for i in selected_indexes]

        note = input("Enter a brief note on why this payload was categorized as such (optional): ")
        releaseTag.reject_reason_note = note

    session.commit()

def categorize(session, release, stream, architecture, showAll, days):
    selectedTags = selectReleases(session, release, stream, architecture, showAll, days)
    while True:
        if len(selectedTags) == 0:
            print("No payloads are available to select, exiting.")
            break
        printReleases(selectedTags)
        val = input("Select tag between 1 and " + str(len(selectedTags)) + " to categorize, enter q to exit: ")
        if val == "q":
            break
        try:
            index = int(val)
            if index > 0 and index <= len(selectedTags):
                categorizeSingle(session, selectedTags[index-1].release_tag)
        except ValueError:
            continue
    session.commit()

def verifyArgs(args):
    if args["days"] < 1:
        print("Please enter a positive number for days.")
        return False
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='View or Update payload reject reasons to DB')
    parser.add_argument('-d', '--dsn', help='Specifies the DSN used to connect to DB', default="postgresql://postgres:@localhost:5432/postgres")
    parser.add_argument("--days", type=int, help="The number of days to query for", default=14)
    subparsers = parser.add_subparsers(title='subcommands', description='valid subcommands', help='Supported operations', required=True)
    list_parser = subparsers.add_parser('list', help='list rejected payloads')
    list_parser.set_defaults(action='list')
    list_parser.add_argument('-r', '--release', help='Specifies a release, like 4.11', default=None)
    list_parser.add_argument('-s', '--stream', help='Specifies a stream, like nightly or ci', default=None)
    list_parser.add_argument('-a', '--arch', help='Specifies an architecture, like amd64', default=None)
    list_parser.add_argument('--all', help='List all rejected payloads. If not specified , list only uncategorized ones.', action='store_true')

    categorize_parser = subparsers.add_parser('categorize', help='categorize a rejected payload')
    categorize_parser.set_defaults(action='categorize')
    categorize_parser.add_argument('-t', '--release_tag', help='Specifies a release payload tag, like 4.11.0-0.nightly-2022-06-25-081133', default=None)
    categorize_parser.add_argument('-r', '--release', help='Specifies a release, like 4.11', default=None)
    categorize_parser.add_argument('-s', '--stream', help='Specifies a stream, like nightly or ci', default=None)
    categorize_parser.add_argument('-a', '--arch', help='Specifies an architecture, like amd64', default=None)
    categorize_parser.add_argument('--all', help='List all rejected payloads. If not specified , list only uncategorized ones.', action='store_true')

    args = vars(parser.parse_args())

    if verifyArgs(args) == False:
        exit(1)

    db = create_engine(args["dsn"])

    Session = sessionmaker(db)
    session = Session()

    base.metadata.create_all(db)

    if args["action"] == "categorize":
        if args["release_tag"]:
            categorizeSingle(session, args["release_tag"])
        else:
            categorize(session, args["release"], args["stream"], args["arch"], args["all"], args["days"])
    else:
        list_releases(session, args["release"], args["stream"], args["arch"], args["all"], args["days"])

