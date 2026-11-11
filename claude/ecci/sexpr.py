"""Minimal, defensive S-expression reader for KiCad files (.kicad_sym / .kicad_sch)."""

from __future__ import annotations

MAX_DEPTH = 200
MAX_BYTES = 64 * 1024 * 1024


class SexprError(ValueError):
    pass


def parse(text: str) -> list:
    """Parse ONE top-level expression into nested lists of str atoms.

    Quoted strings lose their quotes (escapes \\" and \\\\ handled); there is no number
    conversion here (see num()). Raises SexprError on any structural problem.
    """
    if len(text) > MAX_BYTES:
        raise SexprError("input too large")
    i, n = 0, len(text)
    stack: list[list] = []
    root: list | None = None
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(":
            if len(stack) >= MAX_DEPTH:
                raise SexprError("nesting too deep")
            new: list = []
            if stack:
                stack[-1].append(new)
            elif root is not None:
                raise SexprError("multiple top-level expressions")
            stack.append(new)
            if root is None:
                root = new
            i += 1
        elif c == ")":
            if not stack:
                raise SexprError("unbalanced ')'")
            stack.pop()
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while True:
                if j >= n:
                    raise SexprError("unterminated string")
                ch = text[j]
                if ch == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                elif ch == '"':
                    break
                else:
                    buf.append(ch)
                    j += 1
            if not stack:
                raise SexprError("atom outside expression")
            stack[-1].append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()":
                j += 1
            if not stack:
                raise SexprError("atom outside expression")
            stack[-1].append(text[i:j])
            i = j
    if stack:
        raise SexprError("unbalanced '('")
    if root is None:
        raise SexprError("empty input")
    return root


def num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError) as e:
        raise SexprError(f"not a number: {x!r}") from e
    if v != v or v in (float("inf"), float("-inf")):
        raise SexprError("non-finite number")
    return v


def children(node: list, head: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def child(node: list, head: str) -> list | None:
    cs = children(node, head)
    return cs[0] if cs else None
