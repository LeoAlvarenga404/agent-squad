"""Deterministic detector of hostile text (prompt injection, instruction extraction, social engineering).

PURE module: stdlib only, no network, no LLM, no state besides the owner name. Used by the Hermes guard plugin
(alert and write-tool lock) and by the CRM (refuse to store suspicious text). Patterns cover Portuguese and English.

    from agent_squad.guard.detector import analyze, has_instruction, set_owner
    set_owner("Maria Silva")     # optional: also catch "I am Maria, the creator of the system" and the like
    a = analyze(text)            # Analysis(risk 0..3, categories, snippets)
    has_instruction(text, 2)     # True if risk >= 2

How it works:
  1. truncates at 20000 characters;
  2. builds variants of the text: plain (NFKC + lowercase + no accents), clean (no zero-width/control chars,
     Cyrillic/Greek homoglyphs mapped), with spaced-out letters joined, with leetspeak undone, and decodings
     (base64, hex, URL-encoding, HTML entities, \\u escapes, rot13), each one normalized too;
  3. runs every category's rules on every variant and merges the results;
  4. a category found only in a normalized/decoded variant also adds 'obfuscated'.

Scoring: 3 if any high category OR two or more medium ones; 2 if exactly one medium; 1 if only low; else 0.

Anti-backtracking: every quantifier is bounded ({0,N}); gaps between words use tokens with a mandatory
separator (_g), with no ambiguous nested quantifier.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import html
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote

MAX_CHARS = 20000  # one scan window
MAX_WINDOWS = 10  # longer texts are scanned head and tail only and flagged 'oversized'
MAX_DECODES = 32
BUDGET = 6 * MAX_CHARS  # total variant characters (besides plain) analyzed per window
MAX_SNIPPET = 80
MAX_SNIPPETS = 12

HIGH = {"override", "new_identity", "jailbreak", "extraction", "delimiters", "exfiltration"}
MEDIUM = {"authority", "tool_probing", "approval_bypass", "others_data", "oversized"}
LOW = {"pressure"}


@dataclass
class Analysis:
    risk: int
    categories: list[str] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------------------------
_HOMOGLYPHS = str.maketrans({
    # Cyrillic (upper and lower case lookalikes of Latin)
    "А": "a", "В": "b", "Е": "e", "К": "k", "М": "m", "Н": "n", "О": "o", "Р": "p", "С": "c",
    "Т": "t", "У": "y", "Х": "x", "І": "i", "Ј": "j", "Ѕ": "s",
    "а": "a", "е": "e", "ё": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
    "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "к": "k",
    # Greek
    "Α": "a", "Β": "b", "Ε": "e", "Ζ": "z", "Η": "h", "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n",
    "Ο": "o", "Ρ": "p", "Τ": "t", "Υ": "y", "Χ": "x",
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "ω": "w",
    # exotic Latin
    "ɑ": "a", "ɡ": "g", "ı": "i", "ɩ": "i", "ꜱ": "s", "ᴏ": "o", "ᴇ": "e", "ᴀ": "a",
})
_LEET = str.maketrans("013457@$", "oieastas")
_SPACED = re.compile(r"\b[a-z0-9](?:[ ._\-*|/·•][a-z0-9]){1,}\b")


def _invisible(t: str) -> str:
    """Drops zero-width, control and format characters (categories Cc and Cf), except line breaks."""
    return "".join(c for c in t if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf"))


def _strip_accents(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _collapse(t: str) -> str:
    t = re.sub(r"[^\S\n]+", " ", t)
    return re.sub(r" ?\n[ \n]*", "\n", t).strip()


def _raw(t: str) -> str:
    """NFKC without invisibles, case preserved (for case-sensitive rules)."""
    return _invisible(unicodedata.normalize("NFKC", t))


def _plain(t: str) -> str:
    """Only NFKC, lowercase, no accents and collapsed spaces. Leaves zero-width chars and homoglyphs alone."""
    return _collapse(_strip_accents(unicodedata.normalize("NFKC", t).casefold()))


def _clean_nc(t: str) -> str:
    """Cleans without collapsing spaces (needed to undo spaced-out letters)."""
    t = _invisible(unicodedata.normalize("NFKC", t)).translate(_HOMOGLYPHS)
    return _strip_accents(t.casefold())


def _clean(t: str) -> str:
    return _collapse(_clean_nc(t))


def _join_spaced(nc: str) -> str:
    return _collapse(_SPACED.sub(lambda m: re.sub(r"[ ._\-*|/·•]", "", m.group(0)), nc))


def _leet(t: str) -> str:
    return t.translate(_LEET)


# ---------------------------------------------------------------------------------------------
# Decoding (base64, hex, URL, HTML, escapes, rot13)
# ---------------------------------------------------------------------------------------------
_B64 = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{20,}\b")
_HEX_SPACED = re.compile(r"\b(?:[0-9a-fA-F]{2}[ :,-]){9,}[0-9a-fA-F]{2}\b")
_HEX_X = re.compile(r"(?:\\x[0-9a-fA-F]{2}){8,}")
_PCT = re.compile(r"%[0-9a-fA-F]{2}")
_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def _as_text(b: bytes) -> str | None:
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(s) < 8:
        return None
    printable = sum(1 for c in s if c.isprintable() or c in "\n\t")
    letters = sum(1 for c in s if c.isalpha() or c.isspace())
    return s if printable / len(s) >= 0.95 and letters / len(s) >= 0.6 else None


def _decodings(t: str) -> list[str]:
    """Texts obtained by decoding blocks of t. Each item still needs normalizing."""
    out: list[str] = []
    for m in _B64.finditer(t):
        if len(out) >= MAX_DECODES:
            break
        s = m.group(0).rstrip("=").replace("-", "+").replace("_", "/")
        if len(s) % 4 == 1:
            s = s[:-1]
        try:
            d = _as_text(base64.b64decode(s + "=" * (-len(s) % 4)))
        except (binascii.Error, ValueError):
            d = None
        if d:
            out.append(d)
    for rx, clean in ((_HEX_RUN, lambda s: s), (_HEX_SPACED, lambda s: re.sub(r"[ :,-]", "", s)),
                      (_HEX_X, lambda s: s.replace("\\x", ""))):
        for m in rx.finditer(t):
            if len(out) >= 12:
                break
            h = clean(m.group(0))
            try:
                d = _as_text(bytes.fromhex(h[: len(h) // 2 * 2]))
            except ValueError:
                d = None
            if d:
                out.append(d)
    if len(_PCT.findall(t, 0, 4000)) >= 3:
        out.append(unquote(t))
    if "&" in t and re.search(r"&(?:#\d{2,6}|#x[0-9a-fA-F]{2,5}|[a-zA-Z]{2,8});", t):
        out.append(html.unescape(t))
    if "\\u" in t:
        out.append(_UESC.sub(lambda m: chr(int(m.group(1), 16)), t))
    return out


# ---------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------
_SP = r"[^\w\n.!?;]{1,4}"  # word separator within one sentence


def _g(n: int) -> str:
    """Up to n arbitrary words (short gap, never crossing a sentence end)."""
    return rf"(?:\w{{1,20}}{_SP}){{0,{n}}}?"


_END = r"[^\w\n.!?;]{0,4}"


class _R:
    __slots__ = ("cat", "rx", "veto", "raw")

    def __init__(self, cat, pat, veto=None, flags=0, raw=False):
        self.cat, self.rx, self.veto, self.raw = cat, re.compile(pat, flags), veto, raw


def _after(m, t, n=40):
    return t[m.end(): m.end() + n]


def _before(m, t, n=20):
    return t[max(0, m.start() - n): m.start()]


_OWN = r"(?:minhas?|meus?|mandei|falei|passei|enviei|escrevi|disse|pedi|\beu\b|my|\bi\b)"
_OWN_AFTER = re.compile(
    r"^\W*(?:que|q)\W+(?:eu\W+)?(?:\w+\W+)?(?:mandei|passei|falei|disse|enviei|pedi|escrevi|dei|fiz)\b")


def _veto_own(m, t):
    """Ignoring/forgetting what the customer THEMSELVES said (my instructions, what I sent) is fine."""
    return bool(re.search(rf"\b{_OWN}\b", m.group(0)) or _OWN_AFTER.search(_after(m, t)))


# --- override -------------------------------------------------------------------------------
_V_PT = (r"(?:ignor|esquec|desconsider|descart|desobedec|despreze|substitu|anul|apag|sobrescrev|abandon|revog|"
         r"desrespeit|cancel|remov|delet|desativ|suspend|sobreponha|burl|"
         r"deixe\sde\s(?:lado|seguir)|nao\s(?:siga|obedeca|respeite|considere|leve\sem\sconta)|"
         r"pare\sde\s(?:seguir|obedecer)|passe\spor\scima\sd)")
_N_PT = (r"(?:instrucoes|instrucao|regras|regra|prompts?|diretrizes|diretiva|diretivas|orientacoes|configuracoes|"
         r"configuracao|comandos|restricoes|limitacoes|politicas|programacao|treinamento|ordens|protocolos?)")
_V_STRONG = (r"(?:ignor|esquec|desconsider|descart|desobedec|despreze|desrespeit|abandon|"
            r"deixe\sde\s(?:lado|seguir)|nao\s(?:siga|obedeca|respeite|considere|leve\sem\sconta)|pare\sde\s(?:seguir|obedecer))")
# clear reference to the assistant (any verb will do)
_Q_PT = (r"(?:do\ssistema|do\sassistente|do\sagente|do\sbot|do\srobo|da\sia|de\ssistema|de\sseguranca|internas|ocultas|"
         r"secretas|programadas|que\s(?:voce|vc|te|lhe)\s(?:\w+\s){0,2}(?:recebeu|tem|ganhou|possui|segue|usa|deram|"
         r"disseram|passaram|mandaram|enviaram|programaram|configuraram|foi\sdado))")
# only says "previous" (ambiguous: may be the customer's own), needs a strong verb
_Q_WEAK = r"(?:anteriores|acima|previas|originais|iniciais|passadas|antigas|dadas|recebidas)"
_NEG_PREP = r"(?!\s(?:de|do|da|dos|das)\s(?!sistema|configuracao|seguranca))"
_POSS = r"(?:suas?|seus?|tuas?|teus?)"
_ALL_PT = (r"(?:(?:tudo|isso\studo)\s(?:o\s)?(?:que\s)?(?:(?:voce|vc|te|lhe)\s(?:\w+\s){0,2}(?:recebeu|sabe|aprendeu|"
            r"disseram|mandaram|programaram|falaram|deram|passaram|configuraram|tem|possui|foi\sdito)|"
            r"(?:esta\s|vem\s|veio\s|ficou\s|foi\s)?(?:escrito\s|dito\s)?acima|antes\sd[ae]\s(?:esta|essa)\smensagem)|"
            r"o\sque\s(?:(?:o|a|os|as)\s)?(?:\w+\s)?(?:te|lhe)\s(?:disseram|falaram|falou|disse|mandaram|mandou|passaram|"
            r"passou|deram|deu|programaram|programou|configuraram|configurou|instruiram|instruiu|ensinaram|ensinou|pediu)|"
            r"tudo\sacima)")
_V_EN = (r"(?:ignore|disregard|forget|override|discard|neglect|overwrite|supersede|abandon|dismiss|erase|nullify|"
         r"revoke|cancel|delete|stop\sfollowing|do\snot\sfollow|dont\sfollow|don['’]t\sfollow)")
_N_EN = (r"(?:instructions?|rules?|prompts?|guidelines?|directives?|commands?|restrictions?|programming|training|"
         r"constraints|policies|orders|directions|system\sprompt)")

_OVERRIDE = [
    _R("override", rf"\b{_V_PT}\w{{0,8}}{_SP}{_g(4)}{_N_PT}\b{_END}{_g(4)}{_Q_PT}\b", _veto_own),
    _R("override", rf"\b{_V_STRONG}\w{{0,8}}{_SP}{_g(4)}{_N_PT}\b{_END}{_g(4)}{_Q_WEAK}\b", _veto_own),
    _R("override", rf"\b{_V_PT}\w{{0,8}}{_SP}{_g(3)}{_POSS}{_SP}{_g(2)}{_N_PT}\b{_NEG_PREP}", _veto_own),
    _R("override", rf"\b{_V_PT}\w{{0,8}}{_SP}{_g(2)}{_ALL_PT}\b", _veto_own),
    _R("override", rf"\b{_POSS}\s{_N_PT}\s(?:anteriores|originais|antigas|nao\s(?:valem|se\saplicam|existem)|"
                   rf"(?:foram|estao)\s(?:anuladas|revogadas|canceladas|substituidas|suspensas|desativadas|removidas))\b",
       _veto_own),
    _R("override", rf"\b{_V_EN}{_SP}{_g(3)}(?:previous|prior|above|earlier|preceding|original|initial|system|your|all|any|"
                   rf"every|these|those){_SP}{_g(2)}{_N_EN}\b", lambda m, t: bool(re.search(r"\b(?:my|i)\b", m.group(0)))),
    _R("override", rf"\b{_V_EN}{_SP}{_g(2)}(?:the\s)?(?:above|everything\s(?:above|you|that\syou|before|prior|previous)|"
                   rf"all\s(?:the\s)?above|preceding\s(?:text|messages?)|previous\s(?:text|messages?|content|context|"
                   rf"conversation)|prior\s(?:text|messages?|context)|what\s(?:you|they)\s(?:were|have\sbeen)\s(?:told|given)|"
                   rf"anything\s(?:before|above|you\s(?:were|have)))\b"),
    _R("override", r"\bnew\s(?:instructions?|rules|directives?)\s?:|\bstop\W+new\sinstructions?\b"),
    _R("override", rf"\bem\svez\sde\s(?:seguir|obedecer|usar|respeitar|cumprir)\s{_POSS}\s(?:\w+\s)?{_N_PT}\b|"
                   rf"\binstead\sof\s(?:following|obeying|using)\syour\s(?:\w+\s)?{_N_EN}\b"),
    _R("override", rf"\bforget\s(?:everything|all)(?:{_SP}(?:you|above|before|prior|previous|so\sfar|that\syou|and)\b|"
                   rf"[^\w\n]*(?:[.,:;!]|$))", lambda m, t: bool(re.match(r"\W*(?:i|we|about)\b", _after(m, t, 8)))),
    _R("override", rf"\b(?:relax|afrouxe|flexibiliz|suavize|desligue|desative|suspenda|remova|retire)\w{{0,4}}{_SP}"
                   rf"{_g(2)}(?:suas?|seus?|your){_SP}(?:\w+\s)?(?:regras|restricoes|diretrizes|protecoes|filtros|travas|"
                   rf"salvaguardas|limites|seguranca|rules|restrictions|guidelines|safeguards|filters|guardrails)\b"),
]

# --- new_identity ---------------------------------------------------------------------------
_ROLE = (r"(?:(?:o|a|um|uma|meu|minha|seu|sua|the|an?|my)\s)?(?:gerente|admin\w*|dono|dona|chefe|diretor\w*|ceo|root|"
         r"hacker|dan|desenvolvedor\w*|programador\w*|proprietari[oa]|sistema|terminal|outra\sia|outro\s(?:bot|assistente|"
         r"modelo|ia)|uma\sia\s(?:livre|sem|nova|diferente)|assistente\s(?:livre|sem|irrestrito)|robo|personagem|mestre|"
         r"operador\w*|supervisor\w*|auditor\w*|criador\w*|livre|irrestrit[oa]|jailbroken|sem\s(?:regras|limites|restricoes|"
         r"filtros)|autorizad[oa]|manager|administrator|owner|developer|unrestricted|uncensored|unfiltered|god|evil|"
         r"different\sai|another\sai|chatgpt|gpt)")
# "hypothetical" roles (imagine you are...): without owner/manager, which customers use in normal talk
_ROLE_HYP = r"(?!(?:(?:o|a|um|uma|the)\s)?(?:gerente|dono|dona|chefe|diretor|ceo|proprietari|manager|owner))" + _ROLE
_YOU = r"(?:voce|vc|tu)"
_NEW_IDENTITY = [
    _R("new_identity", rf"\b(?:a\spartir\s(?:de\s)?(?:agora|ja|hoje|deste\smomento)|de\sagora\sem\sdiante){_SP}"
                          rf"{_g(2)}{_YOU}\s(?:agora\s)?(?:e|eh|sera|vai\sser|passa\sa\sser|se\stornou)\b",
       lambda m, t: bool(re.match(r"\W*(?:o\s|a\s)?(?:meu|minha)\s(?:contato|atendente|ponto|referencia|responsavel|"
                                  r"parceir|fornecedor|webdesigner|designer)", _after(m, t, 40)))),
    _R("new_identity", rf"\b{_YOU}\s(?:agora\se|e\sagora)\s{_ROLE}\b"),
    _R("new_identity", rf"\b(?:finj\w*|fingir|pretend\w*)\s(?:ser|to\sbe|you\sare|you\sre|that\syou|como\sse|"
                          rf"que\s{_YOU}\s(?:e|eh|era|fosse|seria|agora\se)|{_YOU}\s(?:e|eh|era|fosse|seria))\b"),
    _R("new_identity", rf"\b(?:faca|faz|fazer)\sde\sconta\s(?:que\s)?{_YOU}\s(?:e|eh|era|fosse|seria|nao\stem)\b"),
    _R("new_identity", rf"\b(?:imagin\w+|suponha|vamos\s(?:supor|imaginar|fingir|brincar|jogar)|brincar\sde|"
                          rf"hipoteticamente)\b{_SP}{_g(3)}(?:que\s)?{_YOU}\s(?:\w+\s)?(?:e|eh|era|fosse|seria|sera|"
                          rf"tivesse|nao\stivesse)\s{_g(1)}{_ROLE_HYP}\b"),
    _R("new_identity", rf"\b(?:aja|age|agir|atue|atuar|comporte|haja|trate|comporta)\w{{0,3}}{_SP}{_g(2)}como\s"
                          rf"(?:se\s)?(?:(?:um|uma|o|a)\s)?{_ROLE_HYP}\b"),
    _R("new_identity", rf"\b(?:aja|age|agir|atue|atuar|comporte|haja|trate|comporta|responda|funcione)\w{{0,3}}{_SP}"
                          rf"{_g(2)}como\s(?:se\s)?(?:{_YOU}\s)?(?:nao\s)?(?:tivesse|tem|fosse|possuisse)\s{_g(2)}"
                          rf"(?:regras|limites|restricoes|filtros|etica|moral|limitacoes)\b"),
    _R("new_identity", rf"\b(?:you\sare\snow|you\sre\snow|from\snow\son{_SP}{_g(2)}you\s(?:are|will\sbe|re)|"
                          rf"henceforth\syou|you\swill\s(?:now\s)?(?:be|become|act\sas)|assume\sthe\s(?:role|identity)|"
                          rf"play\sthe\srole\sof)\b",
       lambda m, t: bool(re.match(r"\W*(?:my|our)\s(?:\w+\s)?(?:contact|point|account|designer|developer|rep|representative|"
                                  r"partner)", _after(m, t, 40)))),
    _R("new_identity", rf"\b(?:act(?:ing)?\sas|role\s?play(?:ing)?\sas|behave\s(?:as|like)|simulate\sbeing){_SP}"
                          rf"{_g(2)}{_ROLE_HYP}\b"),
    _R("new_identity", rf"\b(?:imagine|suppose|pretend)\s(?:that\s)?you\s(?:are|were)\s{_g(1)}{_ROLE}\b"),
    _R("new_identity", r"\b(?:pretend|imagine|suppose|role\s?play)\b[^\n.]{0,60}?\byou(?:'re|\sre|\sare|\swere)\s(?:an?\s|the\s)?"
                          r"(?:ai|assistant|bot|model|robot|machine|hacker|unrestricted|uncensored|jailbroken|different|"
                          r"another|dan|god|evil)\b"),
]

# --- jailbreak ------------------------------------------------------------------------------
_DEVICE = re.compile(r"\b(?:celular|android|chrome|navegador|samsung|iphone|telefone|wordpress|firefox|usb|"
                          r"depura\w*|smartphone|tablet|cache|cdn|cloudflare|gastric\w*|cirurgia)\b")
_V_JB_CTX = (r"(?:respond\w*|aja|age|atue|comporte|answer|respond|act|behave|modo|mode|robo|bot|ia|ai|assistente|"
             r"assistant|personagem|voce|vc|you|chatbot|llm|modelo|model)")
_RESTR = (r"(?:filtros?|limites?|restricoes|restricao|regras|censura|etica|barreiras|travas|salvaguardas|limitacoes|"
          r"filters?|limits|restrictions?|rules|censorship|guardrails)")
_JAILBREAK = [
    _R("jailbreak", r"\bdo\sanything\snow\b"),
    _R("jailbreak", r"\bdan\s(?:mode|modo)\b|\bmodo\sdan\b"),
    _R("jailbreak", r"\b(?:modo|mode)\s(?:de\s)?(?:desenvolvedor|developer|dev|deus|god|irrestrito|livre|unrestricted|"
                    r"uncensored|jailbreak\w*|sudo|root|sem\s(?:restricoes?|filtros?|limites?|regras|censura))\b",
       lambda m, t: bool(_DEVICE.search(t[max(0, m.start() - 40): m.end() + 40]))),
    _R("jailbreak", r"\bjailbreak\w*|\bjailbroken\b"),
    _R("jailbreak", r"\bbypass\w*", lambda m, t: bool(_DEVICE.search(t[max(0, m.start() - 40): m.end() + 40]))),
    _R("jailbreak", r"\b(?:unrestricted|uncensored|unfiltered)\s(?:mode|ai|assistant|model|version|responses?|answers?)\b"),
    _R("jailbreak", rf"\b{_V_JB_CTX}\b{_SP}{_g(3)}(?:sem|without|no)\s(?:nenhum[ao]?\s|any\s)?{_RESTR}\b",
       lambda m, t: bool(re.match(r"\W{0,2}(?:de|do|da|dos|das|para|pra|no|na|em|nos|nas|por|of|for)\b", _after(m, t, 12)))),
    _R("jailbreak", rf"\b(?:desativ|desabilit|remov|tire|retire|deslig|disable|remove|turn\soff|deactivate)\w{{0,5}}{_SP}"
                    rf"{_g(2)}(?:suas?|seus?|your|todos\sos|todas\sas|all)\s(?:\w+\s)?{_RESTR}\b"),
    _R("jailbreak", rf"\b(?:{_V_JB_CTX}|\w+)\s(?:sem\snenhum[ao]?|sem\squaisquer|without\sany)\s{_RESTR}\b"),
]

# --- extraction -----------------------------------------------------------------------------
_V_EXT = (r"(?:mostr|exib|revel|repit|copi|cole|colar|imprim|list|traduz|resum|explic|transcrev|diga|dizer|escrev|"
          r"leia|ler|recit|compartilh|informe|fornec|vaze|reproduz|digit|apresent|conte|contar|passe|envie|mande|"
          r"manda|enviar|formate|continue|prossiga|complete|reescrev|parafrase|cite|detalh|qual|quais|quanto|quantas?|"
          r"como|o\sque|onde|"
          r"print|show|tell|reveal|display|output|repeat|copy|paste|share|give|translate|summari[sz]e|explain|recite|"
          r"dump|write|type|send|provide|quote|echo|say|read|what|which|how|format|rewrite|paraphrase|describe|list|"
          r"comece|inicie|start|begin|me\sd[ae]|me\spassa|diz|dizem|dizia|falam|fala|responda|respond|answer|reply)\w{0,6}")
_NEG_DE = (r"(?!\s(?:de|do|da|dos|das)\s(?!sistema|configuracao|seguranca)|"
           r"\s(?:para|pra|sobre|about|for|on|regarding|to)\s(?!(?:o\s|a\s|um\s|uma\s|the\s)?(?:ingles|espanhol|portugues|"
           r"frances|italiano|lingua|idioma|english|spanish|french|portuguese|german|mim|me\b|us\b|codigo|code|bloco|"
           r"lista|tabela|json|markdown|format|table|list|bullet|topicos)))")
_META_POSS = (r"(?:(?:suas?|seus?|tuas?|teus?|your)\s(?:\w+\s){0,2}?(?:prompts?|settings|mensagem\sinicial|texto\sinicial|"
              r"primeira\smensagem|memoria|memory)\b|"
              rf"(?:suas?|seus?|tuas?|teus?|your)\s(?:\w+\s){{0,2}}?(?:instrucoes|instrucao|regras|diretrizes|"
              rf"instructions?|rules|guidelines|directives)\b{_NEG_DE})")
_META_ABS = (r"(?:(?:system|sistema)\sprompt|prompt\s(?:de|do)\ssistema|"
             r"(?:instrucao|instrucoes|regras?|diretrizes?|prompt|configuracao|ferramentas|funcoes|instructions?|rules?)\s"
             r"(?:internas?|ocultas?|secretas?|iniciais|originais|de\ssistema|do\ssistema|do\sassistente|do\sagente|"
             r"que\s(?:voce|vc)\s(?:\w+\s){0,2}(?:recebeu|tem|segue|usa|possui|foi)|que\ste\s(?:deram|passaram|mandaram|"
             r"programaram|configuraram)|you\s(?:were|have\sbeen)\s(?:given|told)|you\sreceived)|"
             r"(?:o\s)?(?:texto|conteudo|tudo|mensagens?|palavras)\s(?:que\s)?(?:esta\s|estao\s|vem\s|veio\s)?acima|"
             r"tudo\sacima|(?:the\s)?(?:text|content|words|message|everything|lines)\sabove|above\s(?:text|content|this\sline)|"
             r"(?:o\s)?que\s(?:voce|vc)\srecebeu(?:\santes)?|o\sque\s(?:te|lhe)\s(?:disseram|falaram|mandaram|passaram|"
             r"deram|programaram)|(?:what|everything)\syou\s(?:were|have\sbeen)\s(?:told|given)|"
             r"your\sinitiali[sz]ation|initiali[sz]ation\s(?:text|above|prompt)|"
             r"(?:hidden|secret|internal|initial|original)\s(?:system\s)?(?:prompt|instructions|rules|guidelines)|"
             r"(?:suas?|seus?|your)\s(?:\w+\s)?(?:mensagem|mensagens|message)\s(?:de|do|of)\s(?:sistema|system)|"
             r"your\s(?:full\s|complete\s|entire\s|system\s)?(?:configuration|setup|settings)|"
             r"(?:o\s)?(?:texto|conteudo|tudo)\s(?:que\s)?(?:vem|veio|esta|estava|existe)\santes\sd[aeo]s?\s(?:minha|nossa|"
             r"esta|essa|primeira)|"
             r"(?:suas?|seus?|your)\s(?:\w+\s)?(?:ferramentas|funcoes|tools|functions)\s(?:que\s)?(?:\w+\s){0,3}?"
             r"(?:disponiveis|available|que\svoce\spode\s(?:chamar|executar|usar)))")
_EXTRACTION = [
    _R("extraction", rf"\b{_V_EXT}\b{_SP}{_g(8)}{_META_POSS}"),
    _R("extraction", rf"\b{_V_EXT}\b{_SP}{_g(8)}{_META_ABS}"),
    _R("extraction", rf"\b{_META_POSS}{_SP}{_g(3)}{_V_EXT}\b"),
    _R("extraction", rf"\b(?:{_META_ABS}){_SP}{_g(3)}(?:repit|copi|mostr|revel|show|repeat|print|diga)\w{{0,5}}\b"),
    _R("extraction", rf"\b(?:primeir|ultim|segund|terceir|proxim|\d+|first|last|second|third)\w{{0,3}}\s(?:\w+\s)?"
                   rf"(?:palavras?|linhas?|frases?|letras?|paragrafos?|caracteres?|words?|lines?|sentences?|letters?|"
                   rf"paragraphs?|characters?)\s(?:d[eao]s?|of)\s{_g(3)}(?:{_META_POSS}|{_META_ABS})"),
    _R("extraction", rf"\b(?:comece|inicie|start|begin|iniciar)\w{{0,3}}{_SP}{_g(4)}(?:com|with)\s(?:a\sfrase\s)?"
                   rf"[\"'«]?(?:voce\se|you\sare|you\sre)\b"),
    _R("extraction", rf"\bcomo\s(?:voce\s|vc\s)?(?:foi\s)?(?:configurad|programad|instruid)\w{{0,2}}\s{_g(3)}"
                   rf"(?:seu|sua|voce|vc)\b|\bcomo\s(?:voce|vc)\sfoi\s(?:configurad|programad|instruid)"),
    _R("extraction", rf"\b(?:liste|mostre|quais|list|show|what)\w{{0,3}}{_SP}{_g(3)}(?:ferramentas|funcoes|tools|functions)"
                   rf"\b{_SP}{_g(5)}(?:tem\sacesso|have\saccess|available\sto\syou|disponiveis\spara\svoce|"
                   rf"que\svoce\s(?:tem|possui|pode\s(?:chamar|executar|invocar)))"),
    _R("extraction", rf"\b(?:o\sque|what)\s{_g(2)}(?:te|lhe)\s(?:disseram|falaram|mandaram|passaram|deram)\b"),
    _R("extraction", r"\b(?:instrucoes|regras|diretrizes|prompts?|instructions|rules)\s(?:secretas?|ocultas?|secret|hidden)\b"),
    _R("extraction", rf"\b(?:liste|mostre|quais|list|show|what)\w{{0,3}}{_SP}{_g(3)}(?:ferramentas|funcoes|tools|functions)\s"
                   rf"(?:que\s)?(?:o\s|a\s|the\s)?(?:assistente|agente|bot|ia|assistant|agent)\s(?:\w+\s)?(?:tem|possui|dispoe|has)\b"),
]

# --- delimiters -----------------------------------------------------------------------------
_CUE = re.compile(r"\b(?:ignor\w*|esquec\w*|nunca|sempre|never|always|must|deve|devera|voce\s(?:e|esta|deve|pode|nao|"
                  r"agora|vai|precisa)|you\s(?:are|must|can|will|should|may|do\snot)|reveal\w*|revel\w*|libere\w*|"
                  r"autoriz\w*|envie|mostre|show|execute|run|send|do\snot|don['’]?t|agora|from\snow|a\spartir|responda|"
                  r"answer|follow|obey|obedeca|siga|enable|disable|ative|desative|permit\w*|allow\w*|sure|"
                  r"of\scourse|here\sis|here\sare|grant\w*|verified|unlock\w*|elevated|approved|concedid\w*|"
                  r"liberad\w*|verificad\w*|conceda|grant)\b")
_LINE_ROLES = r"(?:system|developer|assistant|sistema|desenvolvedor|assistente)"
_DELIMITERS = [
    _R("delimiters", r"<\s?/?\s?(?:system|sys|assistant|developer|instructions?|human|sistema|assistente)\s?>"),
    _R("delimiters", r"<\|[^|>\n]{1,30}\|>"),
    _R("delimiters", r"\[/?inst\]|<</?sys>>|\[/?(?:system|sistema)(?:\s(?:message|prompt|note|notice|override|mensagem|aviso))?\]"),
    _R("delimiters", r"^[ \t]{0,4}#{1,4}[ \t]?(?:system|sistema)[ \t]?:?[ \t]?$|^[ \t]{0,4}#{1,4}[ \t]?(?:instruction|"
                        r"instrucao|human|assistant|response)[ \t]?:", flags=re.M),
    _R("delimiters", r"\b(?:begin|end)\s(?:system|sistema)\s(?:prompt|message)\b|---\s?(?:system|sistema)\s?---|"
                        r"\b(?:system|admin)\soverride\b|\boverride\s(?:code|mode|protocol)\b|\bnew\ssystem\sprompt\b|"
                        r"\bnovo\sprompt\s(?:do\s)?sistema\b|\b(?:inicio|fim)\sdo\sprompt\s(?:do\s)?sistema\b"),
    _R("delimiters", rf"^\W{{0,4}}{_LINE_ROLES}\s?[:\]>)](.{{0,200}})",
       lambda m, t: not _CUE.search(m.group(1)), flags=re.M),
    _R("delimiters", r"^[ \t]{0,4}`{3,}[ \t]?(?:system|sistema|developer|prompt|instructions?)\b", flags=re.M),
    _R("delimiters", r"\b(?:nota|aviso|mensagem|instrucao|instrucoes|atencao|importante|ordem|comando|note|notice|"
                        r"message|instruction|instructions|attention|important)\s(?:\w+\s){0,3}?(?:para|ao|a|to|for)\s"
                        r"(?:o\s|a\s|os\s|the\s)?(?:assistente|agente|ia|llm|bot|modelo|assistant|ai|model|agent|"
                        r"sistema)(?:\s(?:virtual|de\sia|de\sai|ai|artificial|que\s\w+|reading|processing))?\s?[:\-\]]"),
    _R("delimiters", r"\b(?:ai|ia)\s(?:agents?|assistants?|models?|systems?|agentes?)\s?:|"
                        r"\b(?:attention|atencao|ola|prezado|dear|hey|hello)\s(?:\w+\s){0,2}?(?:ai|ia|llm)\s(?:agent|assistant|"
                        r"model|agente|assistente|modelo)\b|\[\s?(?:ai|ia|llm)\s?:"),
    _R("delimiters", r"\b(?:note|nota|important|importante|instruction|instrucao)\s(?:\w+\s){0,2}?to\s(?:ai|llm|all\sai)"
                        r"(?:\s(?:models?|agents?|assistants?|systems?))?\s?[:\-]"),
]

# --- exfiltration ---------------------------------------------------------------------------
_SAFE_URL = re.compile(r"(?:wa\.me|api\.whatsapp|whatsapp\.com|drive\.google|docs\.google|instagram\.com|facebook\.com|"
                         r"maps\.google|goo\.gl|calendly|linktr\.ee|tiktok\.com|linkedin\.com|youtube\.com|youtu\.be|"
                         r"twitter\.com|x\.com|\.gov\.br|receita\.fazenda|bb\.com\.br|caixa\.com\.br|bradesco|itau|santander|"
                         r"nubank|mercadopago|pagseguro|sebrae|registro\.br)")
_FIRST_PERSON = re.compile(r"\b(?:vou|vamos|posso|preciso|quero|irei|devo|tenho\sque|eu|estou|ja)\s(?:\w+\s)?$")
_V_SEND = (r"(?:envi|mand|post|encaminh|transmit|submet|suba|upload|exporte|export|vaz|send|forward|submit|leak|"
          r"push|despache|dispar)")
_EMAIL = r"[a-z0-9._%+\-]{1,40}@[a-z0-9.\-]{1,60}\.[a-z]{2,}"
_DATA = (r"(?:tudo\s(?:o\s)?que\s(?:voce|vc)\s(?:sabe|tem|recebeu|conhece)|historico|conversas?\s(?:completas?|inteiras?)|"
          r"prompt|instrucoes|memoria|senhas?|chaves?(?:\sde\sapi)?|tokens?|credenciais|segredos|lista\sde\s(?:todos\sos\s)?"
          r"clientes|base\sde\s(?:dados|clientes)|banco\sde\sdados|dados\s(?:dos|de\stodos\sos|de\soutros)\sclientes|"
          r"todos\sos\s(?:e-?mails?|contatos|clientes|dados|arquivos)|customer\s(?:database|data|list)|database|"
          r"api\skeys?|passwords?|credentials|secrets?|chat\shistory|conversation|everything\syou\s(?:know|have)|"
          r"all\s(?:the\s)?(?:emails?|contacts|customers|data|files))")
_PARAM = (r"(?:d|data|dados|text|texto|msg|message|mensagem|content|conteudo|info|resp|response|prompt|secret|segredo|"
          r"token|key|chave|chat|history|historico|memoria|memory|cliente|customer|email|phone|telefone|leak|exfil|"
          r"payload|dump|instrucoes|instructions|system|summary|resumo|c)")
_EXFILTRATION = [
    _R("exfiltration", rf"\b{_V_SEND}\w{{0,6}}\s[^\n]{{0,100}}?\b(?:para|pra|to|ao|a|em|at|into|no|na)\s(?:o\s|a\s|the\s)?"
                      rf"(?:(?:endereco|url|link|site|servidor|endpoint|webhook|dominio|server|address)\s)?(?:de\s\w+\s)?"
                      rf"https?:?/{{0,2}}[a-z0-9]",
       lambda m, t: bool(_SAFE_URL.search(t[m.end(): m.end() + 40]) or _FIRST_PERSON.search(_before(m, t, 16)))),
    _R("exfiltration", rf"\b{_V_SEND}\w{{0,6}}\s[^\n]{{0,80}}?\b{_DATA}\b[^\n]{{0,60}}?\b(?:para|pra|to|ao|a)\s(?:\w+\s){{0,2}}?{_EMAIL}"),
    _R("exfiltration", rf"\b{_V_SEND}\w{{0,6}}\s[^\n]{{0,60}}?\b(?:para|pra|to|ao|a)\s(?:\w+\s){{0,2}}?{_EMAIL}[^\n]{{0,60}}?\b{_DATA}\b"),
    _R("exfiltration", rf"!\[[^\]\n]{{0,100}}\]\((?=[^)\s]{{0,200}}(?:[?&]{_PARAM}=|\{{[^}})\s]{{1,40}}\}}|%7b|\[[a-z_]{{2,30}}\]|"
                      rf"<[a-z_]{{2,30}}>|dados|aqui|here|prompt|summary))\s?https?://[^)\s]{{0,200}}\)"),
    _R("exfiltration", rf"<img\s[^>]{{0,100}}src=[\"']?https?://(?=[^>\s\"']{{0,200}}[?&]{_PARAM}=)"),
    _R("exfiltration", r"\b(?:faca|faz|fazer|make|send|do|execute|executa|realize|dispare)\s(?:\w+\s){0,2}?"
                      r"(?:get|post|put|patch|request|requisicao|chamada|fetch|webhook)\s(?:\w+\s){0,2}?(?:para|to|em|at)\s"
                      r"(?:\w+\s){0,2}?https?:?/{0,2}[a-z0-9]",
       lambda m, t: bool(_SAFE_URL.search(t[m.end(): m.end() + 40]))),
    _R("exfiltration", r"https?://\S{1,200}\s[^\n]{0,60}?\b(?:cole|paste|post|poste|envie|send|insira|submit|digite)\w{0,3}\b"
                      r"[^\n]{0,60}?\b(?:resumo|historico|conversa|prompt|instrucoes|dados\sdo\scliente|summary|history|"
                      r"conversation|system)\b"),
    _R("exfiltration", r"\b(?:curl|wget|invoke-webrequest|iwr)\s[^\n]{0,120}?(?:\s-d\s|--data|-x\s?post|--upload|\s-t\s|\$\()"),
    _R("exfiltration", r"\b(?:fetch|requests\.(?:get|post)|axios\.(?:get|post)|http\.(?:get|post))\s?\(\s?[\"']?https?://"),
]

# --- authority ------------------------------------------------------------------------------
_ROLE_TITLE = (r"(?:admin\w*|administrador\w*|criador\w*|programador\w*|desenvolvedor\w*|developer|creator|engenheir\w+|owner|"
          r"dono\sd[oae]s?\s(?:sistema|plataforma|agente|bot|ia|crm|assistente|codigo)|suporte\stecnico|auditor\w*|"
          r"pentester|root|superusuario|operador\w*|responsavel\spel[oa]\s(?:sistema|agente|ia|bot|assistente|plataforma|"
          r"seguranca))")
_CUE_AUTH = re.compile(r"\b(?:autoriz\w*|libera\w*|ordem|ordeno|exijo|determino|obedec\w*|modo\s\w+|acesso\s(?:total|"
                      r"irrestrito|root|admin)|privilegi\w+|override|desbloque\w+|sem\srestric\w+|ignore|desative|"
                      r"mostre|revele|liste|testes?|para\sfins|auditoria|seguranca|verific\w+\s(?:seu|suas|o\ssistema)|"
                      r"voce\s(?:deve|precisa|tem\sque|vai)|you\s(?:must|should|will|can)|reveal|show|authori[sz]e|"
                      r"allow|disable|enable|permission|audit|variaveis|configuracoes|configuracao|print|run|execute|output|"
                      r"diagnostics?|dump|debug|display|exiba|imprima|rode|execute)\b")
_SECOND_PERSON = re.compile(r"\b(?:voce|vc|te|ti|seu|sua|seus|suas|tua|teu|assistente|agente|bot|ia|you|your)\b")
_VETO_ALERT = re.compile(r"\b(?:google|facebook|meta|instagram|banco|gmail|microsoft|apple|whatsapp|paypal|windows|"
                          r"android|iphone|celular|navegador|chrome|antivirus|wordpress|hospedagem|email|e-mail)\b")
_SYS_ALERT = re.compile(r"\b(?:sistema|seguranca|administr\w*|oficial|automatic\w*|system|security|admin\w*|anthropic|"
                         r"openai|deepseek)\b")
_VENDORS = r"(?:anthropic|openai|deepseek)"
_AUTHORITY = [
    _R("authority", rf"\b(?:sou|eu\ssou|aqui\se|somos|i\sam|i\sm|this\sis)\s(?:(?:o|a|um|uma|seu|sua|the|your|an?|"
                     rf"proprio|real|verdadeiro)\s){{0,3}}{_ROLE_TITLE}\b(.{{0,100}})",
       lambda m, t: not (_CUE_AUTH.search(m.group(1)) and _SECOND_PERSON.search(m.group(1)))),
    _R("authority", r"\b(?:sou|eu\ssou)\sseu\s(?:criador|dono|programador|desenvolvedor|administrador|admin|mestre)\b|"
                     r"\bsou\squem\ste\s(?:criou|programou|configurou)\b|\bi\s(?:created|programmed|built|made)\syou\b|"
                     r"\b(?:eu\s)?te\s(?:criei|programei|configurei)\b|"
                     r"\bcomo\s(?:o\s|a\s)?(?:dono|criador|admin\w*|desenvolvedor|programador)\s(?:d[oae]s?|dest[ae]|dess[ae])\s(?:\w+\s)?"
                     r"(?:sistema|plataforma|agente|bot|ia|crm|assistente)\b"),
    _R("authority", r"\b(?:autorizacao|authori[sz]ation|permission|permissao)\s(?:total|irrestrita|completa|maxima|geral|"
                     r"full|de\s(?:administrador|admin|root))\b|\b(?:total|full)\s(?:authori[sz]ation|permissions?)\b|"
                     r"\b(?:acesso\s(?:root|administrativo)|admin(?:istrator)?\s(?:access|privileges|rights|mode)|"
                     r"privilegios?\s(?:de\s)?(?:administrador|admin|root)|root\saccess)\b"),
    _R("authority", r"\b(?:autorizo|autorizamos|i\sauthori[sz]e|te\sautorizo)\s(?:voce|vc|o\sassistente|o\sagente|o\sbot|"
                     r"you|a\svoce|te)\s(?:a\s|to\s)?(?:ignor|liber|revel|mostr|fazer\s(?:qualquer|tudo)|executar|acessar|"
                     r"pular|burlar|ultrapassar|passar\spor|desativ|aprovar|conceder|apagar|deletar|alterar|modificar|"
                     r"reveal|show|bypass|ignore|disable|skip|override|do\s(?:anything|everything))|"
                     r"\b(?:voce|vc)\sesta\sautorizad[oa]\sa\s(?:ignor|liber|revel|mostr|pular|burlar)|"
                     r"\byou\sare\sauthori[sz]ed\sto\s(?:ignore|reveal|bypass|skip|override|disable|do\s(?:anything|everything))"),
    _R("authority", rf"\b(?:a|o|the|da|de)?\s?(?:equipe\s|time\s|team\s)?(?:d[aeo]\s)?{_VENDORS}\b[^\n]{{0,30}}?\b"
                     rf"(?:mandou|autorizou|ordenou|pediu|determinou|disse|instruiu|permitiu|liberou|enviou|exige|quer|says|"
                     rf"told|authori[sz]ed|requires|requested|ordered|instructed|allows|approved|aprovou|exigiu|solicitou)\b"),
    _R("authority", rf"\b(?:this\sis|we\sare|here\sis|aqui\se|somos)\s(?:the\s|a\s|o\s|a\s)?(?:equipe\s|time\s|team\s)?"
                     rf"(?:d[aeo]\s)?{_VENDORS}\b|\b{_VENDORS}\s(?:safety|security|support|trust|seguranca|suporte)\s"
                     rf"(?:team|equipe|time)\b"),
    _R("authority", rf"\b(?:mensagem|comunicado|aviso|message|notice|notification)\s(?:oficial\s)?(?:d[aeo]|from)\s{_VENDORS}\b|"
                     rf"\bsou\sd[ao]\s{_VENDORS}\b|\b(?:suporte|equipe|time|team)\s(?:tecnico\s)?d[aeo]s?\s{_VENDORS}\b|"
                     rf"\bsuporte\stecnico\s(?:da\s|de\s)?{_VENDORS}\b|\bi\s(?:am|work)\s(?:from|at|with)\s{_VENDORS}\b|"
                     rf"\bsou\sdo\ssuporte\s(?:tecnico\s)?(?:d[aeo]\s)?(?:sistema|plataforma|agente)\b"),
    _R("authority", r"(?:^|[\[({*])\s?(?:alerta|aviso|mensagem|notificacao|comunicado|alert|warning|notice|message|"
                     r"notification|system)\b[^\n\])}:]{0,60}[\])}:]",
       lambda m, t: not _SYS_ALERT.search(m.group(0)) or bool(_VETO_ALERT.search(t[m.start(): m.end() + 70])),
       flags=re.M),
]

# --- tool_probing ---------------------------------------------------------------------------
_SLUG_WINDOW = re.compile(r"\b(?:pagina|url|post|blog|link|permalink|artigo|categoria|produto|wordpress|titulo|site|seo|"
                          r"endereco|pagina)\b")
_PROBING = [
    _R("tool_probing", r"\b(?:pedir_aprovacao|identificar_cliente|memoria_registrar|memoria_buscar|mover_estagio|"
                       r"criar_ticket|iniciar_ticket|obter_oportunidade|atualizar_cliente|crm_role|"
                       r"(?:pedir|identificar|memoria|mover|criar|iniciar|obter|atualizar|registrar|buscar|listar|"
                       r"fechar|encerrar|responder)_(?:aprovacao|cliente|registrar|buscar|estagio|ticket|"
                       r"oportunidade|memoria|clientes|tickets)|"
                       r"request_approval|identify_customer|memory_(?:add|search|recent)|move_stage|"
                       r"(?:create|start|get|update|list|claim_next)_tickets?|get_deal|list_deals|update_customer|"
                       r"active_directives|squad_role|squad_scope_slug)\b"),
    _R("tool_probing", r"\bmcp\b|\bestagio\sdo\sfunil\b|\bsoul\.md\b|(?:system|sistema)\sprompt|"
                       r"\bprompt\s(?:de|do)\ssistema\b"),
    _R("tool_probing", r"\bslugs?\b",
       lambda m, t: bool(_SLUG_WINDOW.search(t[max(0, m.start() - 30): m.end() + 30]))),
]
_RAW_RULES = [_R("tool_probing", r"\bSOUL\b", raw=True),
              _R("jailbreak", r"(?:you\sare|voce\s(?:agora\s)?[eé]|act\sas|aja\scomo|finja\sser|pretend\sto\sbe|modo|mode)"
                              r"\W+(?:o\W+|the\W+)?DAN\b", raw=True)]

# --- approval_bypass ------------------------------------------------------------------------
_APPROVAL = (r"(?:aprovacao|aprovacoes|approval|autorizacao|confirmacao|validacao|revisao\shumana|human\sreview|"
          r"human\sapproval|fluxo\sde\saprovacao|etapa\sde\saprovacao|passo\sde\saprovacao)")
_VETO_OWN_APPROVAL = re.compile(r"\b(?:meu|minha|meus|minhas|socio|socia|contador\w*|esposa|marido|nossa|nosso|pai|mae|filho)\b")
_BYPASS = [
    _R("approval_bypass", rf"\b(?:pul|dispens|ignor|desconsider|contorn|evit|burl|desativ|desabilit|salt|skip|bypass|"
                           rf"avoid|waive|overrid|overrul|suspend)\w{{0,6}}{_SP}{_g(3)}(?:a\s|o\s|the\s|essa\s|esta\s|sua\s|its\s)?{_APPROVAL}\b",
       lambda m, t: bool(_VETO_OWN_APPROVAL.search(t[m.start(): m.end() + 25]))),
]


def _owner_rules(o: str) -> tuple[list, list]:
    """Rules naming the business owner (claims to be them, or to act with their blessing)."""
    authority = [
        _R("authority", rf"\b(?:sou|aqui\se|i\sam|this\sis)\s(?:o\s)?{o}\b[^\n.]{{0,30}}?\b(?:criador|dono|admin\w*|"
                         rf"desenvolvedor|programador|responsavel)\s(?:d[oae]s?|dess[ae]|dest[ae])\s(?:sistema|plataforma|"
                         rf"agente|bot|ia|crm|assistente|codigo|projeto)\b"),
        _R("authority", rf"\bo\s{o}\s(?:me\s)?(?:autorizou|liberou|mandou|ordenou|determinou|permitiu)\s(?:voce|vc|te|"
                         rf"que\s(?:voce|vc)|tudo|geral|isso)\b|\bautorizacao\sdo\s{o}\b|\b{o}\s(?:authori[sz]ed|told)\s"
                         rf"(?:you|that\syou)\b"),
    ]
    bypass = [
        _R("approval_bypass", rf"\b(?:nao\s(?:precisa|preciso|necessita|tem\sque|tem\sde|e\snecessari[oa]|peca|pergunte|"
                               rf"aguarde|espere)|don['’]?t|do\snot|no\sneed)\s{_g(3)}(?:de\s|for\s)?(?:{_APPROVAL}|"
                               rf"(?:esperar|aguardar|consultar)\s(?:o\s)?(?:{o}|dono|ninguem|aprovacao)|"
                               rf"(?:ask|wait|request|seek)(?:ing)?\s(?:for\s)?(?:approval|permission|confirmation|authori[sz]ation|"
                               rf"anyone|{o}|the\sowner))\b",
           lambda m, t: bool(_VETO_OWN_APPROVAL.search(t[m.start(): m.end() + 25]))),
        _R("approval_bypass", rf"\bsem\s(?:esperar\s(?:ninguem|aprovacao|o\s{o}|o\sdono|resposta)|(?:pedir|passar\spor)\s"
                               rf"aprovacao|aprovacao|o\s{o}|a\saprovacao|avisar\s(?:o\s{o}|o\sdono|ninguem)|"
                               rf"consultar\s(?:ninguem|o\s{o})|aguardar\s(?:o\s{o}|o\sdono|ninguem))\b",
           lambda m, t: bool(_VETO_OWN_APPROVAL.search(t[m.start(): m.end() + 25]))),
        _R("approval_bypass", rf"\b(?:never|don['’]?t|do\snot|no\sneed\sto)\s(?:ask|wait|request|seek|require)\s(?:for\s)?"
                               rf"(?:approval|permission|confirmation|authori[sz]ation|anyone|{o}|the\sowner)\b"),
    ]
    return authority, bypass

# --- others_data ----------------------------------------------------------------------------
_OTHER_DATA = (r"(?:dados|contratos?|precos?|valores?|descontos?|telefones?|e-?mails?|numeros?|cpfs?|cnpjs?|enderecos?|"
           r"senhas?|historico|conversas?|lista|listas|quanto|nomes?|propostas?|orcamentos?|prices?|contracts?|discounts?|"
           r"phones?|data|details|names?|numbers?|addresses|invoices?|faturas?|pagamentos?|whatsapps?)")
_OTHERS = r"(?:outro|outra|outros|outras|demais|other)"
_CUSTOMERS = r"(?:clientes?|customers?|usuarios|leads|contatos)"
_VETO_MINE = re.compile(r"\b(?:meus?|minhas?|my)\W+(?:\w+\W+)?$")
_OTHERS_DATA = [
    _R("others_data", rf"\b{_OTHER_DATA}\s{_g(4)}(?:d[eoa]s?|of|from|para|pra|pros?|aos?|to|with|com)\s{_g(3)}(?:{_OTHERS}|todos\sos|all)\s(?:seus\s|os\s|your\s|the\s)?{_CUSTOMERS}\b",
       lambda m, t: bool(_VETO_MINE.search(_before(m, t, 15)))),
    _R("others_data", rf"\b{_OTHERS}\s{_g(2)}{_CUSTOMERS}\s{_g(4)}(?:pagou|pagaram|paga|pagam|paid|pay|pays|cobrou|cobra|"
                          rf"cobrado|recebeu|recebem|ganhou|ganharam|tem\sdesconto|got)\b",
       lambda m, t: bool(_VETO_MINE.search(_before(m, t, 15)))),
    _R("others_data", rf"\b(?:lista|list|relacao|planilha|base|cadastro|database|banco)\s{_g(2)}(?:de\s)?(?:todos\sos\sclientes|"
                          rf"(?:seus|suas|vossos|your|all\syour|all\sof\syour)\s(?:\w+\s)?{_CUSTOMERS}|dos\sseus\sclientes)\b"),
    _R("others_data", rf"\b{_OTHER_DATA}\s{_g(4)}(?:com|do|da|de|with|of)\s(?:o\s|a\s|the\s)?(?:cliente|customer)\s"
                          rf"(?:anterior|passado|de\santes|previous|last)\b",
       lambda m, t: bool(_VETO_MINE.search(_before(m, t, 15)))),
    _R("others_data", rf"\b(?:contrato|contract)\s{_g(4)}(?:com|with)\s(?:o\s|a\s|the\s)?{_OTHERS}\s{_CUSTOMERS}\b"),
]

# --- pressure -------------------------------------------------------------------------------
_PRESSURE = [
    _R("pressure", r"\burgent\w*|\burgencia\b|\bagora\smesmo\b|\bimediatamente\b|\bvou\sprocessar\b|"
                  r"\bprocesso\s(?:voces?|a\sempresa)\b|\bprocon\b|\breclame\saqui\b|\badvogado\b|\bultima\schance\b|"
                  r"\bultimo\saviso\b|\bnao\saceito\sdesculpa\w*|\bemergencia\b|\bcliente\s(?:antigo|de\slonga\sdata|fiel)\b|"
                  r"\bo\smais\srapido\spossivel\b|\bexijo\b|\bsob\spena\b|\basap\b|\bright\snow\b|\bimmediately\b|"
                  r"\bi\swill\ssue\b|\bi\sdemand\b"),
]

_OWNER: str | None = None
_ALL_RULES: list = []


def set_owner(name: str | None) -> None:
    """Names the business owner (squad.toml business.owner) so impersonating them, or claiming their blessing to skip
    approval, is caught. Words of 3+ letters count; generic placeholders like "the owner" are ignored."""
    global _OWNER, _ALL_RULES
    words = [w for w in re.findall(r"[a-z]{3,}", _plain(name or "")) if w not in ("the", "owner")]
    alts = list(dict.fromkeys([r"\s".join(words), *words[:1]])) if words else []
    owner = "(?:" + ("|".join(alts) or "(?!)") + ")"
    if owner == _OWNER:
        return
    authority, bypass = _owner_rules(owner)
    _OWNER, _ALL_RULES = owner, (_OVERRIDE + _NEW_IDENTITY + _JAILBREAK + _EXTRACTION + _DELIMITERS + _EXFILTRATION
                                 + _AUTHORITY + authority + _PROBING + _BYPASS + bypass + _OTHERS_DATA + _PRESSURE)


set_owner(None)


def _match(t: str, raw: str | None = None) -> dict[str, list[str]]:
    """Runs every rule on t (already normalized). Raw rules run on the case-preserved text."""
    found: dict[str, list[str]] = {}
    for r in _ALL_RULES:
        for m in r.rx.finditer(t):
            if r.veto and r.veto(m, t):
                continue
            found.setdefault(r.cat, []).append(m.group(0)[:MAX_SNIPPET])
            break  # one snippet per rule is enough for the log
    if raw is not None:
        for r in _RAW_RULES:
            m = r.rx.search(raw)
            if m:
                found.setdefault(r.cat, []).append(m.group(0)[:MAX_SNIPPET])
    return found


def _variants(t: str, plain: str) -> list[str]:
    """Normalized and decoded variants of t, deduplicated and without plain, highest priority first."""
    nc = _clean_nc(t)
    clean = _collapse(nc)
    joined = _join_spaced(nc)
    vs = [clean, joined, _leet(clean), _leet(joined), codecs.decode(clean, "rot13")]
    decod: list[str] = []
    for d in _decodings(t):
        decod.append(d)
        decod.extend(_decodings(d))  # second layer
    for d in sorted(decod[:16], key=len):  # small decoded pieces first (they are cheap)
        c = _clean(d)
        vs += [c, _leet(c)]
    seen, out = {plain}, []
    for v in vs:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _scan(t: str) -> tuple[dict[str, list[str]], bool, bool]:
    """One window: (categories -> snippets, obfuscated, a scan limit was hit)."""
    plain = _plain(t)
    found = _match(plain, _raw(t))
    direct = set(found)
    obfuscated = capped = False
    spent = 0
    for v in _variants(t, plain):
        if spent + len(v) > BUDGET:  # CPU ceiling: what goes unanalyzed is flagged instead of ignored
            capped = True
            continue
        spent += len(v)
        for cat, trs in _match(v).items():
            if cat not in direct:
                obfuscated = True
            found.setdefault(cat, []).extend(trs)
    if len(_B64.findall(t)) > MAX_DECODES:
        capped = True
    if found and any(unicodedata.category(c) == "Cf" for c in t):
        obfuscated = True  # invisible character (zero-width etc.) next to hostile content
    return found, obfuscated, capped


def _windows(t: str) -> tuple[list[str], bool]:
    if len(t) <= MAX_CHARS:
        return [t], False
    step = MAX_CHARS - 500  # overlap so a phrase split between windows is still seen whole
    starts = list(range(0, len(t) - MAX_CHARS + 1, step)) + [len(t) - MAX_CHARS]
    if len(starts) > MAX_WINDOWS:
        return [t[:MAX_CHARS], t[-MAX_CHARS:]], True
    return [t[i:i + MAX_CHARS] for i in starts], False


def analyze(text: str) -> Analysis:
    t = text or ""
    if not t.strip():
        return Analysis(0, [], [])
    windows, oversized = _windows(t)
    found: dict[str, list[str]] = {}
    obfuscated = False
    for w in windows:
        f, o, capped = _scan(w)
        obfuscated, oversized = obfuscated or o, oversized or capped
        for cat, trs in f.items():
            found.setdefault(cat, []).extend(trs)
    if oversized:
        found.setdefault("oversized", [])
    cats = list(found)
    if obfuscated:
        cats.append("obfuscated")
    snippets: list[str] = []
    for c in found:
        for tr in found[c]:
            if tr not in snippets and len(snippets) < MAX_SNIPPETS:
                snippets.append(tr)
    n_high = len(set(cats) & HIGH)
    n_medium = len(set(cats) & MEDIUM)
    if n_high or n_medium >= 2:
        risk = 3
    elif n_medium == 1:
        risk = 2
    elif set(cats) & LOW:
        risk = 1
    else:
        risk = 0
    return Analysis(risk, cats, snippets)


def has_instruction(text: str, min_risk: int = 2) -> bool:
    return analyze(text).risk >= min_risk
