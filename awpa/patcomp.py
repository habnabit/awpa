# Copyright 2006 Google, Inc. All Rights Reserved.
# Licensed to PSF under a Contributor Agreement.

"""Pattern compiler.

The grammar is taken from PatternGrammar.txt.

The compiler compiles a pattern to a pytree.*Pattern instance.
"""

__author__ = "Guido van Rossum <guido@python.org>"

# Python imports
import io
import os

# Fairly local imports
from .pgen2 import driver, literals, tokenize, parse

# Really local imports
from . import load_grammar, pytree


class PatternSyntaxError(Exception):
    pass


def tokenize_wrapper(grammar, input):
    """Tokenizes a string suppressing significant whitespace."""
    token = grammar.token
    skip = {token.NEWLINE, token.INDENT, token.DEDENT}
    tokens = tokenize.generate_tokens(token, io.StringIO(input).readline)
    for quintuple in tokens:
        type, value, start, end, line_text = quintuple
        if type not in skip:
            yield quintuple


class PatternCompiler(object):

    def __init__(self, pygram):
        """Initializer.

        Takes an optional alternative filename for the pattern grammar.
        """
        _, self.grammar, self.syms = load_grammar('pattern')
        self.pygram = pygram
        self.driver = driver.Driver(self.grammar, convert=pattern_convert)
        # Map named tokens to the type value for a LeafPattern
        token = self.pygram.token
        self.token_map = {"NAME": token.NAME,
                          "STRING": token.STRING,
                          "NUMBER": token.NUMBER,
                          "TOKEN": None}

    def compile_pattern(self, input, debug=False, with_tree=False):
        """Compiles a pattern string to a nested pytree.*Pattern object."""
        tokens = tokenize_wrapper(self.grammar, input)
        try:
            root = self.driver.parse_tokens(tokens, debug=debug)
        except parse.ParseError as e:
            raise PatternSyntaxError(str(e))
        if with_tree:
            return self.compile_node(root), root
        else:
            return self.compile_node(root)

    def compile_node(self, node):
        """Compiles a node, recursively.

        This is one big switch on the node type.
        """
        token = self.grammar.token

        # XXX Optimize certain Wildcard-containing-Wildcard patterns
        # that can be merged
        if node.type == self.syms.Matcher:
            node = node.children[0] # Avoid unneeded recursion

        if node.type == self.syms.Alternatives:
            # Skip the odd children since they are just '|' tokens
            alts = [self.compile_node(ch) for ch in node.children[::2]]
            if len(alts) == 1:
                return alts[0]
            p = pytree.WildcardPattern([[a] for a in alts], min=1, max=1)
            return p.optimize()

        if node.type == self.syms.Alternative:
            units = [self.compile_node(ch) for ch in node.children]
            if len(units) == 1:
                return units[0]
            p = pytree.WildcardPattern([units], min=1, max=1)
            return p.optimize()

        if node.type == self.syms.NegatedUnit:
            pattern = self.compile_basic(node.children[1:])
            p = pytree.NegatedPattern(pattern)
            return p.optimize()

        assert node.type == self.syms.Unit

        name = None
        nodes = node.children
        if len(nodes) >= 3 and nodes[1].type == token.EQUAL:
            name = nodes[0].value
            nodes = nodes[2:]
        repeat = None
        if len(nodes) >= 2 and nodes[-1].type == self.syms.Repeater:
            repeat = nodes[-1]
            nodes = nodes[:-1]

        # Now we've reduced it to: STRING | NAME [Details] | (...) | [...]
        pattern = self.compile_basic(nodes, repeat)

        if repeat is not None:
            assert repeat.type == self.syms.Repeater
            children = repeat.children
            child = children[0]
            if child.type == token.STAR:
                min = 0
                max = pytree.HUGE
            elif child.type == token.PLUS:
                min = 1
                max = pytree.HUGE
            elif child.type == token.LBRACE:
                assert children[-1].type == token.RBRACE
                assert  len(children) in (3, 5)
                min = max = self.get_int(children[1])
                if len(children) == 5:
                    max = self.get_int(children[3])
            else:
                assert False
            if min != 1 or max != 1:
                pattern = pattern.optimize()
                pattern = pytree.WildcardPattern([[pattern]], min=min, max=max)

        if name is not None:
            pattern.name = name
        return pattern.optimize()

    def compile_basic(self, nodes, repeat=None):
        token = self.grammar.token

        # Compile STRING | NAME [Details] | (...) | [...]
        assert len(nodes) >= 1
        node = nodes[0]
        if node.type == token.STRING:
            value = str(literals.evalString(node.value))
            return pytree.LeafPattern(_type_of_literal(self.pygram, value), value)
        elif node.type == token.NAME:
            value = node.value
            if value.isupper():
                if value not in self.token_map:
                    raise PatternSyntaxError("Invalid token: %r" % value)
                if nodes[1:]:
                    raise PatternSyntaxError("Can't have details for token")
                return pytree.LeafPattern(self.token_map[value])
            else:
                if value == "any":
                    type = None
                elif not value.startswith("_"):
                    type = getattr(self.pygram.symbols, value, None)
                    if type is None:
                        raise PatternSyntaxError("Invalid symbol: %r" % value)
                if nodes[1:]: # Details present
                    content = [self.compile_node(nodes[1].children[1])]
                else:
                    content = None
                return pytree.NodePattern(type, content)
        elif node.value == "(":
            return self.compile_node(nodes[1])
        elif node.value == "[":
            assert repeat is None
            subpattern = self.compile_node(nodes[1])
            return pytree.WildcardPattern([[subpattern]], min=0, max=1)
        assert False, node

    def get_int(self, node):
        assert node.type == self.grammar.token.NUMBER
        return int(node.value)


def _type_of_literal(grammar, value):
    if value[0].isalpha():
        return grammar.token.NAME
    else:
        return grammar.token.opmap.get(value)


def pattern_convert(grammar, raw_node_info):
    """Converts raw node information to a Node or Leaf instance."""
    type, value, context, children = raw_node_info
    if children or type in grammar.number2symbol:
        return pytree.Node(type, children, context=context)
    else:
        return pytree.Leaf(type, value, context=context)


def compile_pattern(pygram, pattern, **kw):
    return PatternCompiler(pygram).compile_pattern(pattern, **kw)
