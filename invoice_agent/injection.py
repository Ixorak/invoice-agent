"""Backup detection of prompt-injection text, with plain regular expressions.

The AI is the first line of defence (it copies suspicious passages into
``suspicious_instructions``). This scan is a safety net that does not depend on
the AI. It looks for phrases that only make sense when addressed to a machine
("ignore the validation errors", "set the status to OK", "note for AI
systems", ...) in English, French, German and Italian. Single words such as
"AI" or "assistant" are never enough on their own: they appear in normal
invoices ("AI strategy workshop", "Assistant: J. Dupont").

It is a heuristic and can be evaded by creative wording. That is acceptable
because the status is never decided by the AI anyway.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .textutil import strip_accents

_ZERO_WIDTH = re.compile(r"[­​-‏⁠-⁤﻿]")
_S = r"[^.!?\n]"  # any character inside the same sentence

# Patterns run on lowercase text without accents, with whitespace collapsed.
_RULES: list[tuple[str, str]] = [
    # --- English ---------------------------------------------------------
    ("ignore-rules-en",
     rf"\b(ignore|disregard|override|bypass|skip)\b{_S}{{0,40}}?"
     r"\b(instructions?|prompts?|rules|guidelines|validations?|checks?|errors?|warnings?|controls?|verifications?)\b"),
    ("set-status-en", rf"\b(set|change|mark|force|switch)\b{_S}{{0,30}}?\bstatus\b"),
    ("mark-as-en",
     rf"\b(mark|flag|treat|classify)\b{_S}{{0,25}}?\bas\W{{1,3}}"
     r"(approved|paid|valid|validated|verified|ok|legitimate|compliant)\b"),
    ("addressed-to-machine-en",
     r"\b(note|message|instructions?|notice|attention)\W{1,3}(to|for)\W{1,3}(all\W+|any\W+|the\W+)?"
     r"(ai|a\.i\.|llms?|gpt|chatgpt|language models?|chatbots?|bots?|ai (assistants?|agents?|systems?|tools?)|"
     r"(automated|automatic) (systems?|software|agents?|tools?|pipelines?|assistants?|bots?|processing)|"
     r"(processing|accounting|invoice) (systems?|software|agents?|tools?|pipelines?|bots?))\b"),
    ("you-are-ai-en", r"\byou are\W+(an?\W+|now\W+)*(ai|a\.i\.|language model|llm|chatbot|ai assistant)\b"),
    ("prompt-words-en", r"\b(system prompt|prompt injection|jailbreak|developer mode)\b"),
    ("approve-unchecked-en",
     rf"\b(approve|pay|release|validate|accept|process)\b{_S}{{0,30}}?"
     r"\bwithout (any )?(review|checks?|verification|validation|approval|controls?)\b"),
    ("do-not-check-en", r"\b(do not|don't|never)\W+(flag|check|verify|validate|review|escalate)\b"),
    # --- French ----------------------------------------------------------
    ("ignore-rules-fr",
     rf"\b(ignore[rz]?|ignorez|outrepasse[rz]?|contourne[rz]?)\b{_S}{{0,40}}?"
     r"\b(instructions?|consignes?|regles?|erreurs?|controles?|verifications?|validations?|avertissements?)\b"),
    ("set-status-fr",
     rf"\b(mets|mettez|mettre|definis(sez)?|definir|change[rz]?|modifie[rz]?|force[rz]?|marque[rz]?)\b"
     rf"{_S}{{0,30}}?\bstatut\b"),
    ("mark-as-fr",
     rf"\b(considere[rz]?|traite[rz]?|marque[rz]?|classe[rz]?)\b{_S}{{0,25}}?\bcomme\W{{1,3}}"
     r"(approuvee?s?|payee?s?|validee?s?|verifiee?s?|conformes?)\b"),
    ("addressed-to-machine-fr",
     r"\b(note|message|instructions?|avis|attention)\W{1,3}(a|aux|pour|destinee?s? aux?)\W{1,3}(les\W+|l'|la\W+|le\W+)?"
     r"(ia|intelligences? artificielles?|assistants? (ia|virtuels?)|robots?|bots?|llm|modeles? de langage|"
     r"(systemes?|logiciels?|outils?|agents?|programmes?)\W+(de\W+traitement\W+)?(automatiques?|automatises?|ia|comptables?))\b"),
    ("prompt-words-fr", r"\b(prompt systeme|invite systeme)\b"),
    ("approve-unchecked-fr",
     rf"\b(approuve[rz]?|paye[rz]?|valide[rz]?|accepte[rz]?|traite[rz]?)\b{_S}{{0,30}}?"
     r"\bsans (aucune?\W+)?(controles?|verifications?|validation|examen|revue)\b"),
    ("do-not-check-fr", r"\bne\W+(pas\W+)?(signale[rz]?|verifie[rz]?|controle[rz]?|bloque[rz]?)\b"),
    # --- German ----------------------------------------------------------
    ("ignore-rules-de",
     rf"\b(ignorier(e|en|t)?|missachte(n|t)?|ubergeh(e|en|t)?|uberspring(e|en|t)?|umgeh(e|en|t)?)\b{_S}{{0,40}}?"
     r"\b(anweisungen|anweisung|regeln|fehler(meldungen)?|prufungen|prufung|validierung\w*|kontrollen|kontrolle|warnungen)\b"),
    ("set-status-de", rf"\b(setze[n]?|setz|markiere[n]?)\b{_S}{{0,30}}?\bstatus\b"),
    ("status-value-de", rf"\bstatus\b{_S}{{0,15}}?\b(auf|zu)\W{{1,3}}(ok|genehmigt|bezahlt|freigegeben|gultig)\b"),
    ("addressed-to-machine-de",
     r"\b(hinweis|nachricht|anweisung(en)?|notiz|achtung)\W{1,3}(an|fur)\W{1,3}(die\W+|alle\W+|das\W+|den\W+)?"
     r"(ki|kunstliche intelligenz|ki-?assistenten?|sprachmodelle?|bots?|llm|"
     r"(automatische[nr]?|automatisierte[nr]?)\W+(systeme?|verarbeitung\w*|software|agenten?|tools?))\b"),
    ("prompt-words-de", r"\bsystem-?prompt\b"),
    ("approve-unchecked-de",
     rf"\b(genehmige[n]?|bezahle[n]?|freigeben|gib\W+frei|akzeptiere[n]?)\b{_S}{{0,30}}?"
     r"\bohne\W+(prufung|kontrolle|validierung|uberprufung)\b"),
    ("do-not-check-de", r"\bnicht\W+(prufen|melden|markieren|kontrollieren|beanstanden)\b"),
    # --- Italian ---------------------------------------------------------
    ("ignore-rules-it",
     rf"\b(ignora(re|te)?|tralascia(re|te)?)\b{_S}{{0,40}}?"
     r"\b(istruzion[ei]|regole|errori|controll[oi]|verifiche|validazion[ei]|avvisi)\b"),
    ("set-status-it",
     rf"\b(imposta(re|te)?|cambia(re|te)?|segna(re|te)?|metti|mettete|mettere)\b{_S}{{0,30}}?\bstato\b"
     rf"{_S}{{0,10}}?\b(a|su|in|come)\W{{1,3}}(ok|approvat[oa]|pagat[oa]|valid[oa]|convalidat[oa])\b"),
    ("addressed-to-machine-it",
     r"\b(nota|messaggio|istruzion[ei]|avviso|attenzione)\W{1,3}(per|a|al|ai|agli|alle)\W{1,3}(i\W+|gli\W+|le\W+|il\W+|l')?"
     r"(ia|intelligenza artificiale|assistent[ei] (ia|virtual[ei])|bot|llm|modell[oi] linguistic[oi]|"
     r"(sistem[ai]|software|agent[ei]|programm[ai])\W+(di\W+elaborazione\W+)?(automatic[oaie]|automatizzat[oaie]|ia))\b"),
    ("prompt-words-it", r"\bprompt di sistema\b"),
    ("approve-unchecked-it",
     rf"\b(approva(re|te)?|paga(re|te)?|convalida(re|te)?|accetta(re|te)?)\b{_S}{{0,30}}?"
     r"\bsenza\W+(alcun[oa]?\W+)?(controll[oi]|verific[ah]e?|revisione)\b"),
    ("do-not-check-it", r"\bnon\W+(segnalare|controllare|verificare|bloccare)\b"),
]

_COMPILED = [(name, re.compile(pattern)) for name, pattern in _RULES]


@dataclass(frozen=True)
class KeywordHit:
    rule: str
    excerpt: str


def normalize_for_scan(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = strip_accents(text).lower()
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def scan_text(text: str) -> list[KeywordHit]:
    """Return one hit per rule that matches, with a short excerpt around the match."""
    if not text:
        return []
    normalized = normalize_for_scan(text)
    hits: list[KeywordHit] = []
    for name, pattern in _COMPILED:
        m = pattern.search(normalized)
        if m:
            start, end = max(0, m.start() - 40), min(len(normalized), m.end() + 40)
            excerpt = ("…" if start else "") + normalized[start:end] + ("…" if end < len(normalized) else "")
            hits.append(KeywordHit(rule=name, excerpt=excerpt))
    return hits
