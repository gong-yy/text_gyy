"""canonical ePortal COSTING SHEET schema（对照真实表单全量建模）。

所有模板/建单/渲染统一从此取，字段清单：
- 表头 32 字段（含 SO 与合计区等只读计算字段），类型 text/date/boolean/select；
- 产品行 19 列（金额类计算列只读，由 ePortal 计算）；
- 4 个命名附件槽（*合同/报价单/ePO、*J-FORM、*J-FORM (Approval)、GCF），文件内容归 ePortal 管理；
- 智眸 CSV 列 → ePortal 字段映射与产品行拆分。

options 为演示用配置，真实对接时按 ePortal SETUP 配置调整。
"""

# ---- 表头字段：(name, label, type, required, editable, options, group) ----
HEADER_FIELDS: list[tuple] = [
    # 基本信息
    ("Sales Person", "Sales Person", "text", True, True, [], "基本信息"),
    ("Presales", "Presales", "text", False, True, [], "基本信息"),
    ("Quotation Ref / PO No", "Quotation Ref / PO No", "text", True, True, [], "基本信息"),
    ("Customer Name", "Customer Name", "text", True, True, [], "基本信息"),
    ("Customer ID", "Customer ID", "text", True, True, [], "基本信息"),
    ("SO", "SO", "text", False, False, [], "基本信息"),  # ePortal 生成
    ("Date", "Date", "date", True, True, [], "基本信息"),
    ("Exchange Rate (for foreign currency)", "Exchange Rate (for foreign currency)", "text", False, True, [], "基本信息"),
    ("签约公司", "签约公司", "select", True, True, [], "基本信息"),
    # 收货信息
    ("Customer Delivery Address", "Customer Delivery Address", "text", True, True, [], "收货信息"),
    ("End User Name", "End User Name", "text", True, True, [], "收货信息"),
    ("End User Contact", "End User Contact", "text", True, True, [], "收货信息"),
    ("End User E-Mail", "End User E-Mail", "text", False, True, [], "收货信息"),
    ("Estimated Delivery Date To Customer", "Estimated Delivery Date To Customer", "date", True, True, [], "收货信息"),
    # 税务与付款
    ("Tax Structure", "Tax Structure", "select", True, True,
     ["CN VAT13", "CN VAT0", "CN VAT6", "Others"], "税务与付款"),
    ("Customer Payment Term", "Customer Payment Term", "select", True, True,
     ["30D", "45D", "60D", "90D", "120D"], "税务与付款"),
    # 商务信息
    ("已申请了先服务后合同/先备货后合同", "已申请了先服务后合同/先备货后合同", "boolean", False, True, [], "商务信息"),
    ("Sales Bundling", "Sales Bundling (Product with Service)", "boolean", False, True, [], "商务信息"),
    ("JOS/ES Salesman Code", "JOS/ES Salesman Code", "text", False, True, [], "商务信息"),
    ("SF No.", "SF No.", "text", False, True, [], "商务信息"),
    ("Requester", "Requester", "select", False, True, [], "商务信息"),
    ("特别条款", "特别条款", "text", False, True, [], "商务信息"),
    # 计算汇总（ePortal 计算，只读）
    ("产品含税总金额", "产品含税总金额", "text", False, False, [], "计算汇总"),
    ("服务含税总金额", "服务含税总金额", "text", False, False, [], "计算汇总"),
    ("合同含税总金额", "合同含税总金额", "text", False, False, [], "计算汇总"),
    ("产品不含税总金额", "产品不含税总金额", "text", False, False, [], "计算汇总"),
    ("服务不含税总金额", "服务不含税总金额", "text", False, False, [], "计算汇总"),
    ("合同不含税总金额", "合同不含税总金额", "text", False, False, [], "计算汇总"),
    ("产品GP%", "产品GP%", "text", False, False, [], "计算汇总"),
    ("服务GP%", "服务GP%", "text", False, False, [], "计算汇总"),
    ("合同总GP%", "合同总GP%", "text", False, False, [], "计算汇总"),
]

# ---- 产品行列：(col, label, type, required, editable, options) ----
ITEM_COLUMNS: list[tuple] = [
    ("node_id", "Node ID", "select", True, True, []),
    ("biz_category", "BiZ Category", "select", True, True, ["BAU_BIZ", "NEWBIZ"]),
    ("product_part_no", "Product Part No", "text", True, True, []),
    ("vendor_part_no", "Vendor Part No", "text", False, True, []),
    ("description", "Description", "text", True, True, []),
    ("qty", "Qty", "number", True, True, []),
    ("cost_currency", "Cost (Currency)", "select", False, True, ["CNY", "USD", "HKD", "EUR"]),
    ("cost", "Cost", "number", False, True, []),
    ("unit_cost", "Unit Cost (base on cost currency)", "number", False, False, []),   # 计算
    ("price_currency", "Price (Currency)", "select", False, True, ["CNY", "USD", "HKD", "EUR"]),
    ("price", "Price (Currency)", "number", False, True, []),
    ("unit_price", "Unit Price (base on price currency)", "number", True, True, []),
    ("total_cost", "Total Cost", "number", False, False, []),                          # 计算
    ("total_price", "Total Price", "number", False, False, []),                        # 计算
    ("tax_payable", "Tax Payable (RM)", "number", False, False, []),                   # 计算
    ("gp", "GP (base on price currency)", "number", False, False, []),                 # 计算
    ("gp_percent", "GP%", "number", False, False, []),                                 # 计算
    ("supplier", "Supplier", "text", False, True, []),
    ("inventory_type", "Inventory Type", "select", True, True, []),
]

# ---- 命名附件槽（文件内容归 ePortal 管理，T 仅元数据） ----
ATTACHMENT_SLOTS: list[dict] = [
    {"id": "contract_epo", "name": "合同/报价单/ePO", "required": True, "removable": False, "uploaded": False},
    {"id": "jform", "name": "J-FORM", "required": True, "removable": False, "uploaded": False},
    {"id": "jform_approval", "name": "J-FORM (Approval)", "required": True, "removable": False, "uploaded": False},
    {"id": "gcf", "name": "GCF", "required": False, "removable": True, "uploaded": False},
]

# ---- 智眸 CSV 列 → ePortal 字段 ----
ZHIMOU_COLUMN_MAP: dict[str, str] = {
    "customer_name": "Customer Name",
    "signing_company": "签约公司",
    "customer_delivery_address": "Customer Delivery Address",
    "end_user_name": "End User Name",
    "end_user_contact": "End User Contact",
    "tax_structure": "Tax Structure",
    "customer_payment_term": "Customer Payment Term",
    "quotation_ref_po_no": "Quotation Ref / PO No",
}

# 智眸产品行列（; 分隔多行）
ZHIMOU_ITEM_COLUMNS: dict[str, str] = {
    "description_list": "description",
    "qty_list": "qty",
    "unit_price_list": "unit_price",
}

# 智眸元数据列（留痕，不入表单）
ZHIMOU_META_COLUMNS: set[str] = {
    "customer", "file_name", "task_id", "status", "test_mark",
    "product_amount", "product_revenue", "created_at", "recognition_finished_at",
}


def _entry(meta: tuple, value=None) -> dict:
    name, label, type_, required, editable, options, group = meta
    return {
        "value": "" if value is None else value,
        "type": type_,
        "editable": editable,
        "required": required,
        "options": list(options or []),
        "group": group,
        "label": label,
    }


def header_entry(name: str, value=None) -> dict | None:
    """canonical 表头字段的 schema 条目（value 可覆盖）。"""
    for meta in HEADER_FIELDS:
        if meta[0] == name:
            return _entry(meta, value)
    return None


def merge_order_fields(values: dict) -> dict:
    """全量模板：canonical 全部表头字段 + 智眸/传入值覆盖；未知字段追加为通用文本。"""
    fields = {meta[0]: _entry(meta) for meta in HEADER_FIELDS}
    for name, value in (values or {}).items():
        if name in fields:
            fields[name]["value"] = "" if value is None else str(value)
        else:  # 未知字段：通用文本条目（可编辑）
            fields[name] = {
                "value": "" if value is None else str(value),
                "type": "text", "editable": True, "required": False,
                "options": [], "group": "其他", "label": name,
            }
    return fields


def default_item_schema() -> dict:
    return {
        col: {"editable": editable, "required": required,
              "type": type_, "options": list(options or []), "label": label}
        for col, label, type_, required, editable, options in ITEM_COLUMNS
    }


def default_attachments() -> list[dict]:
    return [dict(slot) for slot in ATTACHMENT_SLOTS]


def split_zhimou_items(fields: dict) -> tuple[dict, list[dict]]:
    """拆出智眸产品行列（; 分隔多行），其余字段原样返回。

    行数以 qty/unit_price 等结构列为准：描述文本本身可能含分号（如会议室清单），
    描述段数多于行数时，多余段落并回最后一行的描述文本。
    """
    item_keys = set(ZHIMOU_ITEM_COLUMNS)
    scalars = {k: v for k, v in (fields or {}).items() if k not in item_keys}
    parts: dict[str, list[str]] = {
        ZHIMOU_ITEM_COLUMNS[k]: [p.strip() for p in str(fields.get(k) or "").split(";")]
        for k in item_keys if fields.get(k) not in (None, "")
    }
    if not parts:
        return scalars, []
    structural = [len(parts[c]) for c in ("qty", "unit_price") if c in parts]
    n = max(structural) if structural else 1
    if "description" in parts and len(parts["description"]) > n:
        merged = parts["description"][: n - 1] + ["; ".join(p for p in parts["description"][n - 1:] if p)]
        parts["description"] = merged
    items = []
    for i in range(n):
        row = {col: (vals[i] if i < len(vals) else "") for col, vals in parts.items()}
        row = {k: v for k, v in row.items() if v != ""}
        if row:
            items.append(row)
    return scalars, items


def zhimou_row_to_intake(row: dict) -> dict:
    """智眸 CSV 一行 → /api/intake 请求体（客户名 + 映射字段 + 元数据）。"""
    fields = {}
    meta = {}
    for col, value in (row or {}).items():
        value = (value or "").strip() if isinstance(value, str) else value
        if col in ZHIMOU_COLUMN_MAP:
            fields[ZHIMOU_COLUMN_MAP[col]] = value
        elif col in ZHIMOU_ITEM_COLUMNS:
            fields[col] = value  # 产品行列交给 split_zhimou_items
        elif col in ZHIMOU_META_COLUMNS:
            meta[col] = value
    customer = (fields.get("Customer Name") or "").strip() or str(meta.get("customer", "") or "").strip()
    return {"customer_name": customer, "fields": fields, "meta": meta}
