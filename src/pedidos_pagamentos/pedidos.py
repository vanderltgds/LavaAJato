"""Ordens de serviço, itens, desconto, autorização de fiado e entrega."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from src.compartilhado.banco_modulos import conectar, preparar_banco


CENTAVO = Decimal("0.01")


def _dinheiro(valor, nome="valor"):
    try:
        numero = Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{nome.capitalize()} inválido.")
    if not numero.is_finite() or numero < 0:
        raise ValueError(f"{nome.capitalize()} não pode ser negativo.")
    return float(numero)


def _recalcular(conexao, pedido_id):
    soma = conexao.execute(
        "SELECT COALESCE(SUM(subtotal), 0) FROM pedido_itens WHERE pedido_id=?", (pedido_id,)
    ).fetchone()[0]
    pedido = conexao.execute("SELECT desconto FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    desconto = float(pedido["desconto"] or 0)
    total = round(max(float(soma) - desconto, 0), 2)
    conexao.execute("UPDATE pedidos SET valor_total=? WHERE id=?", (total, pedido_id))
    return total


def criar_pedido(cliente_id=None, agendamento_id=None, observacao="", *, veiculo_id=None,
                 veiculo_descricao="", veiculo_placa="", origem="balcao", fila_id=None):
    """Cria uma OS. origem pode ser 'agendamento', 'fila' ou 'balcao'."""
    if origem not in {"agendamento", "fila", "balcao"}:
        raise ValueError("Origem deve ser agendamento, fila ou balcao.")
    if origem == "agendamento" and agendamento_id is None:
        raise ValueError("Informe o agendamento de origem.")
    if origem == "fila" and fila_id is None:
        raise ValueError("Informe o registro da fila de origem.")
    preparar_banco()
    conexao = conectar()
    try:
        conexao.execute("BEGIN IMMEDIATE")
        cursor = conexao.execute(
            "INSERT INTO pedidos (cliente_id, agendamento_id, observacao, origem, fila_id, "
            "veiculo_id, veiculo_descricao, veiculo_placa) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cliente_id, agendamento_id, observacao, origem, fila_id, veiculo_id,
             veiculo_descricao, veiculo_placa),
        )
        pedido_id = cursor.lastrowid
        numero = f"OS-{pedido_id:06d}"
        conexao.execute("UPDATE pedidos SET numero=? WHERE id=?", (numero, pedido_id))
        conexao.commit()
        return pedido_id
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def adicionar_item(pedido_id, descricao, quantidade, valor_unitario, produto_id=None, tipo=None):
    """Adiciona serviço ou produto. Produtos são baixados do estoque na mesma transação."""
    if not descricao or not descricao.strip():
        raise ValueError("A descrição do item é obrigatória.")
    if isinstance(quantidade, bool) or not isinstance(quantidade, int) or quantidade <= 0:
        raise ValueError("A quantidade deve ser um inteiro positivo.")
    valor_unitario = _dinheiro(valor_unitario, "valor unitário")
    tipo = tipo or ("produto" if produto_id is not None else "servico")
    if tipo not in {"servico", "produto"}:
        raise ValueError("Tipo do item deve ser servico ou produto.")
    if tipo == "produto" and produto_id is None:
        raise ValueError("Informe o produto cadastrado para baixar o estoque.")
    if tipo == "servico" and produto_id is not None:
        raise ValueError("Serviços não podem possuir produto_id.")

    preparar_banco()
    conexao = conectar()
    try:
        conexao.execute("BEGIN IMMEDIATE")
        pedido = conexao.execute("SELECT status FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not pedido or pedido["status"] == "cancelado":
            raise ValueError("Pedido não encontrado ou cancelado.")
        if pedido["status"] == "pago":
            raise ValueError("Não é possível alterar um pedido pago.")
        if tipo == "produto":
            produto = conexao.execute(
                "SELECT nome, preco_venda, quantidade FROM produtos WHERE id=? AND ativo=1", (produto_id,)
            ).fetchone()
            if not produto:
                raise ValueError("Produto não encontrado ou inativo.")
            if produto["quantidade"] < quantidade:
                raise ValueError("Estoque insuficiente para este produto.")
            descricao = produto["nome"]
            valor_unitario = _dinheiro(produto["preco_venda"], "preço do produto")
            conexao.execute("UPDATE produtos SET quantidade=quantidade-? WHERE id=?", (quantidade, produto_id))
            conexao.execute(
                "INSERT INTO estoque_movimentacoes (produto_id, tipo, quantidade, motivo, pedido_id) "
                "VALUES (?, 'saida', ?, ?, ?)", (produto_id, quantidade, "Venda na ordem de serviço", pedido_id)
            )
        subtotal = _dinheiro(Decimal(str(valor_unitario)) * quantidade, "subtotal")
        conexao.execute(
            "INSERT INTO pedido_itens (pedido_id, produto_id, descricao, quantidade, valor_unitario, subtotal, tipo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pedido_id, produto_id, descricao.strip(), quantidade, valor_unitario, subtotal, tipo),
        )
        _recalcular(conexao, pedido_id)
        conexao.commit()
        return True
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def definir_desconto(pedido_id, desconto):
    desconto = _dinheiro(desconto, "desconto")
    preparar_banco()
    conexao = conectar()
    try:
        conexao.execute("BEGIN IMMEDIATE")
        pedido = conexao.execute("SELECT status FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not pedido or pedido["status"] != "aberto":
            raise ValueError("Pedido não encontrado ou não está aberto.")
        bruto = conexao.execute(
            "SELECT COALESCE(SUM(subtotal),0) FROM pedido_itens WHERE pedido_id=?", (pedido_id,)
        ).fetchone()[0]
        if desconto > float(bruto):
            raise ValueError("O desconto não pode ser maior que a soma dos itens.")
        conexao.execute("UPDATE pedidos SET desconto=? WHERE id=?", (desconto, pedido_id))
        total = _recalcular(conexao, pedido_id)
        conexao.commit()
        return total
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def buscar_pedido(pedido_id):
    preparar_banco()
    conexao = conectar()
    try:
        pedido = conexao.execute(
            "SELECT p.*, c.nome AS cliente_nome FROM pedidos p "
            "LEFT JOIN clientes c ON c.id=p.cliente_id WHERE p.id=?", (pedido_id,)
        ).fetchone()
        if not pedido:
            return None
        resultado = dict(pedido)
        resultado["itens"] = [dict(l) for l in conexao.execute(
            "SELECT * FROM pedido_itens WHERE pedido_id=? ORDER BY id", (pedido_id,)
        ).fetchall()]
        resultado["pagamentos"] = [dict(l) for l in conexao.execute(
            "SELECT * FROM pagamentos WHERE pedido_id=? ORDER BY id", (pedido_id,)
        ).fetchall()]
        resultado["saldo"] = _saldo_conexao(conexao, pedido_id, resultado["valor_total"])
        return resultado
    finally:
        conexao.close()


def listar_pedidos(cliente_id=None, status=None, status_pagamento=None, data_inicio=None, data_fim=None):
    preparar_banco()
    conexao = conectar()
    try:
        sql = ("SELECT p.*, c.nome AS cliente_nome FROM pedidos p "
               "LEFT JOIN clientes c ON c.id=p.cliente_id WHERE 1=1")
        dados = []
        for campo, valor in (("p.cliente_id", cliente_id), ("p.status", status),
                             ("p.status_pagamento", status_pagamento)):
            if valor is not None:
                sql += f" AND {campo}=?"; dados.append(valor)
        if data_inicio:
            sql += " AND date(p.data)>=date(?)"; dados.append(data_inicio)
        if data_fim:
            sql += " AND date(p.data)<=date(?)"; dados.append(data_fim)
        sql += " ORDER BY p.data DESC, p.id DESC"
        return [dict(l) for l in conexao.execute(sql, dados).fetchall()]
    finally:
        conexao.close()


def _saldo_conexao(conexao, pedido_id, total=None):
    if total is None:
        pedido = conexao.execute("SELECT valor_total FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not pedido:
            return None
        total = pedido["valor_total"]
    pago = conexao.execute(
        "SELECT COALESCE(SUM(valor),0) FROM pagamentos WHERE pedido_id=? AND status='confirmado'",
        (pedido_id,)
    ).fetchone()[0]
    total, pago = round(float(total), 2), round(float(pago), 2)
    return {"valor_total": total, "valor_pago": pago, "saldo": round(max(total-pago, 0), 2)}


def autorizar_fiado(pedido_id, autorizado_por, motivo=""):
    """Autoriza entrega sem quitação; exige identificação do gerente/responsável."""
    if not autorizado_por or not autorizado_por.strip():
        raise ValueError("Identifique o gerente que autorizou o fiado.")
    preparar_banco()
    conexao = conectar()
    try:
        cursor = conexao.execute(
            "UPDATE pedidos SET fiado_autorizado=1, autorizado_por=?, "
            "observacao=CASE WHEN ?='' THEN observacao ELSE COALESCE(observacao,'') || '\nFiado: ' || ? END "
            "WHERE id=? AND status='aberto'",
            (autorizado_por.strip(), motivo.strip(), motivo.strip(), pedido_id),
        )
        conexao.commit()
        if cursor.rowcount != 1:
            raise ValueError("Pedido não encontrado ou não está aberto.")
        return True
    finally:
        conexao.close()


def pode_entregar(pedido_id):
    preparar_banco()
    conexao = conectar()
    try:
        pedido = conexao.execute(
            "SELECT status, status_pagamento, fiado_autorizado FROM pedidos WHERE id=?", (pedido_id,)
        ).fetchone()
        return bool(pedido and pedido["status"] != "cancelado" and
                    (pedido["status_pagamento"] == "pago" or pedido["fiado_autorizado"]))
    finally:
        conexao.close()


def registrar_entrega(pedido_id):
    """Marca a entrega somente se estiver pago ou houver fiado autorizado."""
    preparar_banco()
    conexao = conectar()
    try:
        cursor = conexao.execute(
            "UPDATE pedidos SET entregue_em=CURRENT_TIMESTAMP WHERE id=? AND status!='cancelado' "
            "AND (status_pagamento='pago' OR fiado_autorizado=1) AND entregue_em IS NULL", (pedido_id,)
        )
        conexao.commit()
        if cursor.rowcount != 1:
            raise ValueError("Entrega bloqueada: quite o pedido ou obtenha autorização de fiado do gerente.")
        return True
    finally:
        conexao.close()


def cancelar_pedido(pedido_id):
    preparar_banco()
    conexao = conectar()
    try:
        conexao.execute("BEGIN IMMEDIATE")
        pedido = conexao.execute("SELECT status FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not pedido or pedido["status"] == "cancelado":
            conexao.rollback(); return False
        pagamentos = conexao.execute(
            "SELECT COUNT(*) FROM pagamentos WHERE pedido_id=? AND status='confirmado'", (pedido_id,)
        ).fetchone()[0]
        if pagamentos:
            raise ValueError("Estorne os pagamentos com motivo antes de cancelar o pedido.")
        itens = conexao.execute(
            "SELECT produto_id, quantidade FROM pedido_itens WHERE pedido_id=? AND produto_id IS NOT NULL",
            (pedido_id,)
        ).fetchall()
        for item in itens:
            conexao.execute("UPDATE produtos SET quantidade=quantidade+? WHERE id=?",
                            (item["quantidade"], item["produto_id"]))
            conexao.execute(
                "INSERT INTO estoque_movimentacoes (produto_id, tipo, quantidade, motivo, pedido_id) "
                "VALUES (?, 'estorno', ?, ?, ?)",
                (item["produto_id"], item["quantidade"], "Devolução por cancelamento da OS", pedido_id)
            )
        conexao.execute("UPDATE pedidos SET status='cancelado', status_pagamento='estornado' WHERE id=?", (pedido_id,))
        conexao.commit()
        return True
    except Exception:
        conexao.rollback(); raise
    finally:
        conexao.close()


def gerar_recibo(pedido_id):
    """Retorna os dados completos para impressão ou geração de PDF do recibo."""
    pedido = buscar_pedido(pedido_id)
    if not pedido:
        return None
    saldo = pedido["saldo"]
    return {
        "numero": pedido["numero"], "data": pedido["data"],
        "cliente": pedido.get("cliente_nome") or f"Cliente #{pedido.get('cliente_id') or 'não informado'}",
        "veiculo": pedido.get("veiculo_descricao") or "Não informado",
        "placa": pedido.get("veiculo_placa") or "Não informada",
        "itens": pedido["itens"], "subtotal": round(float(pedido["valor_total"]) + float(pedido["desconto"]), 2),
        "desconto": float(pedido["desconto"]), "total": float(pedido["valor_total"]),
        "pagamentos": pedido["pagamentos"], "valor_pago": saldo["valor_pago"],
        "saldo": saldo["saldo"], "status_pagamento": pedido["status_pagamento"],
    }
