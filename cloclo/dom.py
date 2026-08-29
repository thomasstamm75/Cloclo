"""Mini-DOM et moteur de sélecteurs CSS, basés uniquement sur la stdlib.

Le parseur est tolérant : il ferme implicitement les balises ouvertes non
refermées (`<p>`, `<li>`, `<tr>`...), ignore les fermetures orphelines et
conserve le texte brut des `<script>` / `<style>` sans l'interpréter.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
RAW_TEXT_TAGS = frozenset(("script", "style", "template"))

# Balises fermées implicitement par l'ouverture d'une balise du même groupe.
IMPLIED_END = {
    "p": frozenset(
        "address article aside blockquote details div dl fieldset figcaption"
        " figure footer form h1 h2 h3 h4 h5 h6 header hr main nav ol p pre"
        " section table ul".split()
    ),
    "li": frozenset(("li",)),
    "dt": frozenset(("dt", "dd")),
    "dd": frozenset(("dt", "dd")),
    "option": frozenset(("option", "optgroup")),
    "thead": frozenset(("tbody", "tfoot")),
    "tbody": frozenset(("tbody", "tfoot")),
    "tr": frozenset(("tr",)),
    "td": frozenset(("td", "th", "tr")),
    "th": frozenset(("td", "th", "tr")),
}

# Balises dont le contenu textuel n'appartient pas au texte visible.
NON_TEXT_TAGS = frozenset(("script", "style", "noscript", "template", "svg", "head"))

BLOCK_TAGS = frozenset(
    "address article aside blockquote br div dl dt dd fieldset figcaption figure"
    " footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section table"
    " tbody td tfoot th thead tr ul".split()
)

_WS = re.compile(r"[ \t\r\f\v ]+")
_NL = re.compile(r"\n{3,}")


class Node:
    """Un élément, un nœud texte (`tag == "#text"`) ou le document (`"#document"`)."""

    __slots__ = ("tag", "attrs", "children", "parent", "data", "_classes")

    def __init__(self, tag, attrs=None, data=""):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = None
        self.data = data
        self._classes = None

    # -- construction ----------------------------------------------------
    def append(self, node):
        node.parent = self
        self.children.append(node)
        return node

    # -- accès -----------------------------------------------------------
    @property
    def is_element(self):
        return not self.tag.startswith("#")

    @property
    def classes(self):
        if self._classes is None:
            self._classes = frozenset(self.attrs.get("class", "").split())
        return self._classes

    def get(self, name, default=""):
        return self.attrs.get(name, default)

    def signature(self):
        """Empreinte structurelle : sert à repérer les fratries répétées."""
        cls = " ".join(sorted(self.classes))
        return f"{self.tag}.{cls}"

    def depth(self):
        n, d = self.parent, 0
        while n is not None:
            d += 1
            n = n.parent
        return d

    # -- parcours --------------------------------------------------------
    def walk(self):
        """Tous les descendants, en profondeur d'abord (le nœud exclu)."""
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            yield node
            if node.children:
                stack.extend(reversed(node.children))

    def elements(self):
        for node in self.walk():
            if node.is_element:
                yield node

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def child_elements(self):
        return [c for c in self.children if c.is_element]

    def find(self, *tags):
        """Premier descendant portant l'une des balises demandées."""
        wanted = frozenset(tags)
        for node in self.walk():
            if node.tag in wanted:
                return node
        return None

    def find_all(self, *tags):
        wanted = frozenset(tags)
        return [n for n in self.walk() if n.tag in wanted]

    def select(self, selector):
        return select(self, selector)

    def select_one(self, selector):
        found = select(self, selector)
        return found[0] if found else None

    def has_ancestor(self, *tags):
        wanted = frozenset(tags)
        return any(a.tag in wanted for a in self.ancestors())

    # -- texte -----------------------------------------------------------
    def text(self, sep=" "):
        """Texte visible, espaces normalisés."""
        parts = []
        self._collect_text(parts)
        return _WS.sub(" ", sep.join(parts)).strip()

    def block_text(self):
        """Texte visible en conservant les sauts de ligne entre blocs."""
        parts = []
        self._collect_text(parts, blocks=True)
        raw = "".join(parts)
        raw = "\n".join(_WS.sub(" ", line).strip() for line in raw.split("\n"))
        return _NL.sub("\n\n", raw).strip()

    def _collect_text(self, out, blocks=False):
        if self.tag == "#text":
            out.append(self.data)
            return
        if self.tag in NON_TEXT_TAGS:
            return
        block = blocks and self.tag in BLOCK_TAGS
        if block:
            out.append("\n")
        for child in self.children:
            child._collect_text(out, blocks)
            if not blocks:
                out.append(" ")
        if block:
            out.append("\n")

    def raw_text(self):
        """Texte de tous les nœuds descendants, filtres de visibilité inclus.

        Nécessaire pour lire le contenu des `<script>` (JSON-LD).
        """
        parts = []
        stack = [self]
        while stack:
            node = stack.pop()
            if node.tag == "#text":
                parts.append(node.data)
            else:
                stack.extend(reversed(node.children))
        return "".join(parts)

    def text_length(self):
        return len(self.text())

    def html(self):
        """Sérialisation HTML du contenu du nœud."""
        out = []
        for child in self.children:
            child._serialize(out)
        return "".join(out)

    def outer_html(self):
        out = []
        self._serialize(out)
        return "".join(out)

    def _serialize(self, out):
        if self.tag == "#text":
            out.append(escape_text(self.data))
            return
        if self.tag == "#comment":
            return
        if self.tag == "#document":
            for child in self.children:
                child._serialize(out)
            return
        attrs = "".join(
            f' {k}="{escape_attr(v)}"' for k, v in self.attrs.items() if v is not None
        )
        out.append(f"<{self.tag}{attrs}>")
        if self.tag in VOID_TAGS:
            return
        for child in self.children:
            child._serialize(out)
        out.append(f"</{self.tag}>")

    def __repr__(self):
        if self.tag == "#text":
            return f"#text({self.data[:24]!r})"
        cls = self.attrs.get("class", "")
        return f"<{self.tag}{'.' + cls if cls else ''}>"


def escape_text(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(value):
    return escape_text(value).replace('"', "&quot;")


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]
        self._raw_tag = None

    @property
    def current(self):
        return self.stack[-1]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        implied = IMPLIED_END.get(self.current.tag)
        if implied and tag in implied:
            self.stack.pop()
        # Une seconde ouverture de <html>/<body> ne recrée pas de niveau.
        if tag in ("html", "body", "head") and any(n.tag == tag for n in self.stack):
            return
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self.current.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)
        if tag in RAW_TEXT_TAGS:
            self._raw_tag = tag

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self.current.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == self._raw_tag:
            self._raw_tag = None
        if tag in VOID_TAGS:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return
        # Fermeture orpheline : on l'ignore.

    def handle_data(self, data):
        if data:
            self.current.append(Node("#text", data=data))

    def handle_comment(self, data):
        self.current.append(Node("#comment", data=data))

    def error(self, message):  # pragma: no cover - requis par HTMLParser < 3.10
        pass


def parse(html):
    """Analyse une chaîne HTML et renvoie le nœud `#document`."""
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# Sélecteurs CSS (sous-ensemble)
# ---------------------------------------------------------------------------

_SIMPLE = re.compile(
    r"""
    (?P<tag>^[a-zA-Z][\w-]*|^\*)
  | \.(?P<cls>[\w-]+)
  | \#(?P<id>[\w-]+)
  | \[(?P<attr>[\w:-]+)(?:(?P<op>[~^$*|]?=)(?P<quote>["']?)(?P<val>[^\]]*?)(?P=quote))?\]
  | :(?P<pseudo>[\w-]+)(?:\((?P<arg>[^)]*)\))?
""",
    re.VERBOSE,
)


class _Compound:
    """Suite de conditions portant sur un même élément (ex. `a.btn[href]`)."""

    __slots__ = ("tag", "classes", "ids", "attrs", "pseudos")

    def __init__(self):
        self.tag = None
        self.classes = []
        self.ids = []
        self.attrs = []
        self.pseudos = []

    def matches(self, node):
        if not node.is_element:
            return False
        if self.tag and self.tag != "*" and node.tag != self.tag:
            return False
        if any(c not in node.classes for c in self.classes):
            return False
        if any(node.get("id") != i for i in self.ids):
            return False
        for name, op, value in self.attrs:
            if name not in node.attrs:
                return False
            actual = node.attrs[name]
            if op is None:
                continue
            if op == "=" and actual != value:
                return False
            if op == "^=" and not actual.startswith(value):
                return False
            if op == "$=" and not actual.endswith(value):
                return False
            if op == "*=" and value not in actual:
                return False
            if op == "~=" and value not in actual.split():
                return False
            if op == "|=" and actual != value and not actual.startswith(value + "-"):
                return False
        for name, arg in self.pseudos:
            if not _match_pseudo(node, name, arg):
                return False
        return True


def _match_pseudo(node, name, arg):
    parent = node.parent
    if name == "not":
        return not any(c.matches(node) for c in (_parse_compound(arg),))
    if name == "first-child":
        return bool(parent) and parent.child_elements()[:1] == [node]
    if name == "last-child":
        return bool(parent) and parent.child_elements()[-1:] == [node]
    if name in ("nth-child", "nth-of-type"):
        if not parent:
            return False
        siblings = parent.child_elements()
        if name == "nth-of-type":
            siblings = [s for s in siblings if s.tag == node.tag]
        try:
            wanted = int(arg)
        except (TypeError, ValueError):
            return False
        return siblings.index(node) + 1 == wanted
    if name == "has":
        return bool(select(node, arg))
    if name == "empty":
        return not node.text()
    return False


def _parse_compound(chunk):
    compound = _Compound()
    position = 0
    chunk = chunk.strip()
    while position < len(chunk):
        match = _SIMPLE.match(chunk, position)
        if not match:
            raise ValueError(f"sélecteur invalide : {chunk!r}")
        if match.group("tag"):
            compound.tag = match.group("tag").lower()
        elif match.group("cls"):
            compound.classes.append(match.group("cls"))
        elif match.group("id"):
            compound.ids.append(match.group("id"))
        elif match.group("attr"):
            compound.attrs.append(
                (match.group("attr").lower(), match.group("op"), match.group("val"))
            )
        elif match.group("pseudo"):
            compound.pseudos.append((match.group("pseudo"), match.group("arg")))
        position = match.end()
    return compound


def _parse_selector(selector):
    """Renvoie une liste de `(combinateur, compound)` de gauche à droite."""
    tokens = re.split(r"\s*([>+~])\s*|\s+", selector.strip())
    steps = []
    combinator = " "
    for token in tokens:
        if token is None or token == "":
            continue
        if token in (">", "+", "~"):
            combinator = token
            continue
        steps.append((combinator, _parse_compound(token)))
        combinator = " "
    if not steps:
        raise ValueError("sélecteur vide")
    return steps


_SELECTOR_CACHE = {}


def compile_selector(selector):
    groups = _SELECTOR_CACHE.get(selector)
    if groups is None:
        groups = [_parse_selector(part) for part in selector.split(",") if part.strip()]
        if not groups:
            raise ValueError("sélecteur vide")
        _SELECTOR_CACHE[selector] = groups
    return groups


def _matches_steps(node, steps):
    combinator, compound = steps[-1]
    if not compound.matches(node):
        return False
    if len(steps) == 1:
        return True
    rest = steps[:-1]
    if combinator == " ":
        return any(_matches_steps(a, rest) for a in node.ancestors())
    if combinator == ">":
        return node.parent is not None and _matches_steps(node.parent, rest)
    siblings = node.parent.child_elements() if node.parent else []
    index = siblings.index(node) if node in siblings else -1
    if combinator == "+":
        return index > 0 and _matches_steps(siblings[index - 1], rest)
    if combinator == "~":
        return any(_matches_steps(s, rest) for s in siblings[:index])
    return False


def select(root, selector):
    """Tous les descendants de `root` correspondant au sélecteur CSS."""
    groups = compile_selector(selector)
    seen, out = set(), []
    for node in root.elements():
        if id(node) in seen:
            continue
        if any(_matches_steps(node, steps) for steps in groups):
            seen.add(id(node))
            out.append(node)
    return out


def matches(node, selector):
    return any(_matches_steps(node, steps) for steps in compile_selector(selector))
