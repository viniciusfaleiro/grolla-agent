"""PDF do post mortem com fpdf2 (puro Python, sem dependências de sistema).

Fonte core Helvetica: o texto vai em latin-1, que cobre os acentos do português; o que não
couber é trocado pelo equivalente mais próximo. Contrato do post mortem: o corpo é do
grolla-integracoes.
"""

from grolla.domain.incidents import Incident
from grolla.domain.post_mortem import PostMortem


class FpdfDocumentRenderer:
    def render(self, pm: PostMortem, incident: Incident) -> bytes:
        raise NotImplementedError
