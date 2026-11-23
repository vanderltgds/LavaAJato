"""API pública do módulo de pedidos e pagamentos."""
from .pedidos import (
    criar_pedido, adicionar_item, definir_desconto, buscar_pedido, listar_pedidos,
    cancelar_pedido, autorizar_fiado, pode_entregar, registrar_entrega, gerar_recibo,
)
from .pagamentos import (
    registrar_pagamento, estornar_pagamento, listar_pagamentos, listar_estornos,
    consultar_saldo, resumo_caixa, fechar_caixa, consultar_fechamento,
)

__all__ = [
    "criar_pedido", "adicionar_item", "definir_desconto", "buscar_pedido", "listar_pedidos",
    "cancelar_pedido", "autorizar_fiado", "pode_entregar", "registrar_entrega", "gerar_recibo",
    "registrar_pagamento", "estornar_pagamento", "listar_pagamentos", "listar_estornos",
    "consultar_saldo", "resumo_caixa", "fechar_caixa", "consultar_fechamento",
]
