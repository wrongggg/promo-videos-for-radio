"""CLI for managing admin access. Anyone can sign in with Google and use the
app -- there's no allowlist. This only controls who gets admin features
(personalized /poplock curation, /analytics).

Usage:
    python server/manage_users.py add-admin <email>
    python server/manage_users.py remove-admin <email>
    python server/manage_users.py list-admins
"""
import argparse

import users


def cmd_add(args):
    users.add_admin(args.email)
    print(f"{args.email} is now an admin.")


def cmd_remove(args):
    users.remove_admin(args.email)
    print(f"{args.email} is no longer an admin.")


def cmd_list(args):
    admins = users.list_admins()
    if not admins:
        print("No admins yet.")
        return
    for email in admins:
        print(f"  {email}")


def main():
    parser = argparse.ArgumentParser(description="Manage admin access.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add-admin", help="Grant admin (personal /poplock + /analytics)")
    p_add.add_argument("email")
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove-admin", help="Revoke admin")
    p_remove.add_argument("email")
    p_remove.set_defaults(func=cmd_remove)

    p_list = sub.add_parser("list-admins", help="List all admins")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
