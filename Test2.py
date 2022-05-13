#!/usr/bin/env python3
import argparse

def getArgs(argv=None):
    parser = argparse.ArgumentParser(description="calculate X to the power of Y")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="store_true")
    group.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("x", type=int, help="the base")
    parser.add_argument("y", type=int, help="the exponent")
    return parser.parse_args(argv)

if __name__ == "__main__":

    argvals = None             # init argv in case not testing
    argvals = '6 2 -v'.split() # example of passing test params to parser
    args = getArgs(argvals)

    answer = args.x**args.y

    if args.quiet:
        print(answer)
    elif args.verbose:
        print("{} to the power {} equals {}".format(args.x, args.y, answer))
    else:
        print("{}^{} == {}".format(args.x, args.y, answer))