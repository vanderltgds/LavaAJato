"""Pagamentos, estornos, saldo e fechamento diário de caixa."""
from datetime import date
from src.compartilhado.banco_modulos import conectar, preparar_banco

FORMAS = {"dinheiro", "pix", "debito", "credito"}


def _atualizar_status(conexao, pedido_id):
    pedido = conexao.execute("SELECT valor_total, status FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not pedido or pedido["status"] == "cancelado":
        return
    pago = conexao.execute(
        "SELECT COALESCE(SUM(valor),0) FROM pagamentos WHERE pedido_id=? AND status='confirmado'",
        (pedido_id,)
    ).fetchone()[0]
    total = round(float(pedido["valor_total"]), 2)
    pago = round(float(pago), 2)
    if pago >= total and total > 0:
        status_pagamento = "pago"
    elif pago > 0:
        status_pagamento = "parcial"
    else:
        houve_estorno = conexao.execute(
            "SELECT 1 FROM estornos_pagamento WHERE pedido_id=? LIMIT 1", (pedido_id,)
        ).fetchone()
        status_pagamento = "estornado" if houve_estorno else "pendente"
    conexao.execute("UPDATE pedidos SET status_pagamento=? WHERE id=?", (status_pagamento, pedido_id))
    # Mantém o status operacional antigo para não quebrar integrações existentes.
    conexao.execute("UPDATE pedidos SET status=? WHERE id=?", ("pago" if status_pagamento == "pago" else "aberto", pedido_id))


def registrar_pagamento(pedido_id, valor, forma, referencia=""):
    try:
        valor = round(float(valor), 2)
    except (TypeError, ValueError):
        raise ValueError("Valor de pagamento inválido.")
    if valor <= 0 or forma not in FORMAS:
        raise ValueError("Informe um valor positivo e uma forma: dinheiro, pix, debito ou credito.")
    preparar_banco()
    conexao = conectar()
    try:
        conexao.execute("BEGIN IMMEDIATE")
        pedido = conexao.execute("SELECT valor_total, status FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not pedido or pedido["status"] == "cancelado":
            raise ValueError("Pedido não encontrado ou cancelado.")
        if pedido["status"] == "pago":
            raise ValueError("Este pedido já está pago.")
        pagos = conexao.execute(
            "SELECT COALESCE(SUM(valor),0) FROM pagamentos WHERE pedido_id=? AND status='confirmado'",
            (pedido_id,)
        ).fetchone()[0]
        saldo = round(float(pedido["valor_total"]) - float(pagos), 2)
        if valor > saldo + 0.001:
            raise ValueError(f"O pagamento excede o saldo do pedido (R$ {saldo:.2f}).")
        cursor = conexao.execute(
            "INSERT INTO pagamentos (pedido_id, valor, forma, status, referencia) "
            "VALUES (?, ?, ?, 'confirmado', ?)", (pedido_id, valor, forma, referencia)
        )
        _atualizar_status(conexao, pedido_id)
        conexao.commit()
        return cursor.lastrowid
    except Exception:
        conexao.rollback(); raise
    finally:
        conexao.close()


def estornar_pagamento(pagamento_id, motivo, autorizado_por=""):
    """Estorna integralmente um pagamento, exigindo motivo e preservando auditoria."""
    if not motivo or not motivo.strip():
        raise ValueError("O motivo do estorno é obrigatório.")
    preparar_banco()
    conexao = conectar()
    try:
        conexao.execute("BEGIN IMMEDIATE")
        pagamento = conexao.execute(
            "SELECT * FROM pagamentos WHERE id=? AND status='confirmado'", (pagamento_id,)
        ).fetchone()
        if not pagamento:
            raise ValueError("Pagamento não encontrado ou já estornado.")
        conexao.execute(
            "UPDATE pagamentos SET status='estornado', estornado_em=CURRENT_TIMESTAMP WHERE id=?",
            (pagamento_id,)
        )
        conexao.execute(
            "INSERT INTO estornos_pagamento (pagamento_id, pedido_id, valor, motivo, autorizado_por) "
            "VALUES (?, ?, ?, ?, ?)",
            (pagamento_id, pagamento["pedido_id"], pagamento["valor"], motivo.strip(), autorizado_por.strip())
        )
        _atualizar_status(conexao, pagamento["pedido_id"])
        conexao.commit()
        return True
    except Exception:
        conexao.rollback(); raise
    finally:
        conexao.close()


def listar_pagamentos(pedido_id):
    preparar_banco()
    conexao = conectar()
    try:
        return [dict(l) for l in conexao.execute(
            "SELECT * FROM pagamentos WHERE pedido_id=? ORDER BY data DESC, id DESC", (pedido_id,)
        ).fetchall()]
    finally:
        conexao.close()


def listar_estornos(pedido_id=None):
    preparar_banco()
    conexao = conectar()
    try:
        sql = "SELECT e.*, p.forma, p.referencia FROM estornos_pagamento e JOIN pagamentos p ON p.id=e.pagamento_id"
        dados = ()
        if pedido_id is not None:
            sql += " WHERE e.pedido_id=?"; dados = (pedido_id,)
        sql += " ORDER BY e.data DESC, e.id DESC"
        return [dict(l) for l in conexao.execute(sql, dados).fetchall()]
    finally:
        conexao.close()


def consultar_saldo(pedido_id):
    preparar_banco()
    conexao = conectar()
    try:
        pedido = conexao.execute("SELECT valor_total FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not pedido:
            return None
        pago = conexao.execute(
            "SELECT COALESCE(SUM(valor),0) FROM pagamentos WHERE pedido_id=? AND status='confirmado'",
            (pedido_id,)
        ).fetchone()[0]
        total, pago = round(float(pedido["valor_total"]), 2), round(float(pago), 2)
        return {"valor_total": total, "valor_pago": pago, "saldo": round(max(total-pago, 0), 2)}
    finally:
        conexao.close()


def resumo_caixa(data_caixa=None):
    """Soma recebimentos confirmados por forma na data informada (AAAA-MM-DD)."""
    data_caixa = data_caixa or date.today().isoformat()
    preparar_banco()
    conexao = conectar()
    try:
        linhas = conexao.execute(
            "SELECT forma, COALESCE(SUM(valor),0) AS total FROM pagamentos "
            "WHERE status='confirmado' AND date(data)=date(?) GROUP BY forma", (data_caixa,)
        ).fetchall()
        totais = {forma: 0.0 for forma in sorted(FORMAS)}
        for linha in linhas:
            if linha["forma"] in totais:
                totais[linha["forma"]] = round(float(linha["total"]), 2)
        totais["total_geral"] = round(sum(totais.values()), 2)
        totais["data_caixa"] = data_caixa
        return totais
    finally:
        conexao.close()


def fechar_caixa(data_caixa=None, fechado_por="", observacao=""):
    """Grava o fechamento diário; não permite fechar duas vezes a mesma data."""
    data_caixa = data_caixa or date.today().isoformat()
    resumo = resumo_caixa(data_caixa)
    preparar_banco()
    conexao = conectar()
    try:
        cursor = conexao.execute(
            "INSERT INTO caixa_fechamentos (data_caixa, total_dinheiro, total_pix, total_debito, "
            "total_credito, total_geral, fechado_por, observacao) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (data_caixa, resumo["dinheiro"], resumo["pix"], resumo["debito"], resumo["credito"],
             resumo["total_geral"], fechado_por, observacao)
        )
        conexao.commit()
        resumo["fechamento_id"] = cursor.lastrowid
        return resumo
    except Exception as erro:
        conexao.rollback()
        if "UNIQUE constraint failed" in str(erro):
            raise ValueError("O caixa desta data já foi fechado.")
        raise
    finally:
        conexao.close()


def consultar_fechamento(data_caixa):
    preparar_banco()
    conexao = conectar()
    try:
        linha = conexao.execute("SELECT * FROM caixa_fechamentos WHERE data_caixa=?", (data_caixa,)).fetchone()
        return dict(linha) if linha else None
    finally:
        conexao.close()
