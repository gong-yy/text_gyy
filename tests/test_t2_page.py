"""T2 页面契约：本地 order_id 启动、T 系统登录态与 ePortal 同步重发。"""


def test_t2_page_uses_local_order_bootstrap(client):
    page = client.get("/t2").text
    assert "/api/orders/${orderId}" in page
    assert "/api/orders/lookup" in page
    assert "/lock" not in page
    assert "/save" in page
    assert 'params.get("id")' in page
    assert '/api/eportal/ticket-orders/${eportalId}' in page
    assert 'params.get("token")' not in page
    assert 'get("order_id")' in page
    assert 'get("intellisight_id")' in page
    assert 'get("form_id")' not in page
    # 进入时不展示历史回写状态；提交后直接显示 ePortal 的成功/失败详情。
    assert "statusBadge(order.status)" not in page
    assert "重新同步" not in page
    assert "showSubmitResult" in page


def test_t2_page_uses_t_system_login_and_renders_local_fields(client):
    page = client.get("/t2").text
    assert "/static/common.js" in page
    assert "API.user" in page
    assert "data-field" in page
    assert "readonly" in page
    # 当前 T2 以 ePortal 表单号展示订单上下文；旧版“本地订单编号”文案已移除。
    assert "ePortal 表单" in page


def test_t2_page_uses_costing_sheet_t_layout_and_dialog_editing(client):
    page = client.get("/t2").text

    assert "COSTING SHEET - T" in page
    assert 'class="sheet"' in page
    assert "product-table" in page
    assert "当前附件总大小" in page
    assert "编辑字段" in page
    assert 'openProductField' in page
    assert "同步并提交" in page
    for removed in ("新建", "另存", "导出EXCEL", "转到T系统"):
        assert removed not in page


def test_t2_page_preserves_eportal_costing_sheet_structure(client):
    """T2 必须使用 ePortal 源码的固定表单布局，而非通用两列表格。"""
    page = client.get("/t2").text

    # ePortal 的灰色页首、完整导航与 Costing Sheet 固定表单区。
    assert 'background: #CCC' in page
    assert 'New SaleOrder(so)' in page
    assert 'New OrderToPurchase(OTP)' in page
    assert 'SaleOrder List' in page
    assert 'Customer Delivery Address:' in page
    assert 'End User Name (To receive the item):' in page
    assert 'Estimated Delivery Date To Customer:' in page
    assert 'Sales Bundling (Product &amp; Services):' in page
    # ePortal 原件的产品、金额汇总与附件区域均须保留视觉结构。
    assert 'Product Part No' in page
    assert 'Product Amount' in page
    assert '当前附件总大小' in page


def test_t2_page_uses_eportal_column_and_modal_alignment_rules(client):
    page = client.get("/t2").text

    # 不再采用等宽 24 列；产品列和修改原因/保存方式均按 ePortal 样式对齐。
    assert "/static/style.css" in page
    assert 'input[name="saveMode"]' in page
    assert 'class="save-mode"' in page
    assert ".box-body .save-mode input" in page


def test_t2_page_matches_eportal_product_attachment_and_summary_geometry(client):
    """Prevent the replica from collapsing ePortal's wide detail areas."""
    page = client.get("/t2").text

    # The live ePortal header is a 23-column sequence, ending in OTP.
    headers = (
        "No.", "Node ID", "BiZ Category", "Product Part No", "Vendor Part No",
        "Description", "Qty", "Cost", "Unit Cost", "Price", "Unit Price",
        "Total Cost", "Total Price", "Tax Payable", "GP", "GP%", "Supplier",
        "Inventory Type", "Warehouse", "Dropship", "Comments", "Notes", "OTP",
    )
    header_html = page[page.index("<thead>"):page.index("</thead>")]
    positions = [header_html.index(header) for header in headers]
    assert positions == sorted(positions)

    # Amounts remain a left-aligned three-row ePortal block, while attachments
    # reserve the original 900px, three-column footprint.
    assert 'class="summary-table"' in page
    assert 'class="summary-label"' in page
    assert 'class="attachment-table"' in page
    assert 'width="900"' in page
    assert '合同原件：' in page
    assert 'data-attachment-action' in page


def test_t2_page_prefills_conversion_rule_instead_of_change_reason(client):
    page = client.get("/t2").text

    assert "转换规则（可选）" in page
    assert "lastConversionRule" in page
    assert "修改原因（可选）" not in page


def test_t2_page_owns_clickable_eportal_radios_and_scaled_canvas(client):
    """ePortal radio controls must remain real controls, not be replaced by common.js."""
    page = client.get("/t2").text

    assert 'id="portalCanvas"' in page
    assert 'name="prior"' in page
    assert 'name="original"' in page
    assert "fitPortal" in page
    assert "openCustomerSearch" in page


def test_t2_page_has_both_source_customer_search_entries_and_plain_table_surface(client):
    page = client.get("/t2").text

    # ePortal 源码在 Customer Name 和 Customer ID 两行均提供客户检索入口。
    assert 'id="customerSearch"' in page
    assert 'id="customerIdSearch"' in page
    assert 'customerIdSearch")?.addEventListener("click",()=>openCustomerSearch("customer_id"))' in page
    # 产品表沿用源码的白底蓝色列头，不留下黑色 Product Detail 遮罩。
    assert '<div class="fixed-note bg-black">Product Detail</div>' not in page
    assert ".product-table th{background:#fff" in page
    assert "html,body{background:#fff" in page
    assert "white-space:nowrap" in page


def test_t2_header_grid_matches_the_nine_row_eportal_layout(client):
    page = client.get("/t2").text

    # 源码的顶部提示区没有常驻 SO 输入框；SO 是左栏第六行字段。
    assert 'SO：<input id="so1"' not in page
    for field in ("sales_person", "presales", "quotation_ref", "customer_name", "customer_id", "so", "date", "exchange_rate", "location"):
        assert f'data-field="{field}"' in page
    for field in ("customer_address", "user_name", "user_contact", "user_mail", "delivery_date", "tax_structure", "customer_payment_term", "sales_bundling", "es_salesman_code"):
        assert f'data-field="{field}"' in page
    for label in ("SF No.", "Requester", "特别条款"):
        assert label in page


def test_t2_uses_scrollable_canvas_and_customer_result_dialog(client):
    page = client.get("/t2").text

    assert "#viewport{width:auto;height:auto;overflow:visible" in page
    assert "#portalCanvas{width:2400px" in page
    assert 'id="customerQuery"' in page
    assert 'id="customerResults"' in page
    assert "customerResults" in page
    assert "applyCustomer" in page


def test_t2_uses_source_page_scrolling_with_compact_header_fields(client):
    """不锁定内部滚动容器；顶部字段沿用源码的 13px 和自然行高。"""
    page = client.get("/t2").text

    assert "html,body{background:#fff;overflow:auto}" in page
    assert "#viewport{width:auto;height:auto;overflow:visible" in page
    assert "#portalCanvas{width:2400px" in page
    assert ".form-table td{height:21px;padding:1px 2px;font-size:10px}" in page


def test_t2_keeps_compact_canvas_with_a_visible_top_horizontal_scrollbar(client):
    """宽表不必滚到底部；顶部滚动条与 2400px 内容画布同时存在。"""
    page = client.get("/t2").text

    assert 'id="topHorizontalScroll"' in page
    assert 'id="topHorizontalScrollContent"' in page
    assert "#portalCanvas{width:2400px" in page
    assert ".form-table td{height:21px;padding:1px 2px;font-size:10px}" in page
    assert "syncHorizontalScroll" in page


def test_t2_header_values_use_compact_widths_and_truncate_customer_address(client):
    """地址不得撑开布局，普通值列保持短横线宽度；Sales Bundling 无虚线。"""
    page = client.get("/t2").text

    assert ".form-table .field-value{min-width:0;width:132px" in page
    assert "[data-field=\"customer_address\"]{width:360px" in page
    assert "text-overflow:ellipsis" in page
    assert "[data-field=\"sales_bundling\"]{border-bottom:0" in page


def test_t2_header_moves_third_column_fields_left_without_changing_grid_width(client):
    page = client.get("/t2").text

    assert '<td></td><td colspan="2" class="cen">SF No.</td>' not in page
    assert '<td></td><td colspan="2" class="cen">Requester</td>' not in page
    assert '<td></td><td colspan="2" class="cen">特别条款</td>' not in page
    assert '<td colspan="2" class="cen">SF No.</td><td colspan="3"><span data-field="sf_no"' in page
    assert '<td colspan="2" class="cen">Requester</td><td colspan="3"><span data-field="buyer_1"' in page
    assert '<td colspan="2" class="cen">特别条款</td><td colspan="4"><span data-field="term"' in page
    assert '<span data-field="sales_bundling" class="field-value"></span></td><td colspan="2"><label' in page
    assert '<td colspan="10"></td></tr>' in page


def test_t2_edits_each_product_cell_in_its_own_field_dialog(client):
    page = client.get("/t2").text

    assert 'data-product-field="${key}"' in page
    assert 'openProductField(Number(cell.closest("[data-product]").dataset.product),cell.dataset.productField)' in page
    assert 'openDialog("编辑字段"' in page
    assert 'type:"product_field",index,key' in page
    assert 'items[editing.index][editing.key]=$("editValue").value' in page


def test_t2_displays_eportal_pass_state_for_each_product_row(client):
    page = client.get("/t2").text

    assert 'if(key==="otp")return row.otp??row.pass??""' in page
    assert 'items.map((row,index)' in page
    assert 'data-product="${index}"' in page


def test_t2_product_dropdown_fields_stay_editable_and_are_not_overwritten_on_render(client):
    page = client.get("/t2").text

    assert 'const editableProductFields=new Set([' in page
    for field in ('"node_id"', '"biz_category"', '"currency"', '"price"', '"tax_pyable"', '"dropship"'):
        assert field in page
    assert 'if(!row.node_id)row.node_id=node' in page
    assert 'if(!row.warehouse)row.warehouse=' in page


def test_t2_displays_saved_dropdown_values_with_the_same_labels_as_the_menu(client):
    page = client.get("/t2").text

    assert 'function displayFieldValue(key,val){return dropdownLabels[key]?.[val]??val}' in page
    assert 'node.textContent=displayFieldValue(key,val==null?"":val)' in page


def test_t2_makes_editable_summary_amounts_use_source_style_underlines(client):
    page = client.get("/t2").text

    assert '.summary-table .amount.editable-amount{min-width:96px;border:0;border-bottom:1px solid #999' in page
    assert '.summary-table .amount.editable-amount:hover{background:#cff' in page
    assert 'node.classList.toggle("editable-amount",!readonly)' in page


def test_t2_product_row_number_uses_the_source_style_action_menu(client):
    page = client.get("/t2").text

    assert 'id="productImportFile"' in page
    assert 'data-product-action="add"' in page
    assert 'data-product-action="delete"' in page
    assert 'data-product-action="import"' in page
    assert 'function addProductRow(index)' in page
    assert 'function deleteProductRow(index)' in page
    assert 'function importProductRows(file)' in page


def test_t2_uses_table_click_delegation_for_all_editable_product_fields(client):
    page = client.get("/t2").text

    assert 'productRowsBody.onclick=event=>' in page
    assert 'event.target.closest("[data-product-field]")' in page
    assert 'event.target.closest("[data-product-action]")' in page


def test_t2_binds_each_product_cell_button_directly_to_its_editor(client):
    page = client.get("/t2").text

    assert 'openProductField(Number(cell.closest("[data-product]").dataset.product),cell.dataset.productField)' in page


def test_t2_opens_the_single_field_editor_from_each_product_data_cell(client):
    """产品数据或空白格本身必须是可点击的编辑入口，列标题不能承担编辑操作。"""
    page = client.get("/t2").text

    assert 'data-product-field="${key}"' in page
    assert 'event.target.closest("[data-product-field]")' in page
    assert 'openProductField(' in page
    assert 'data-product-column-field' not in page


def test_t2_highlights_only_editable_product_cells(client):
    """可编辑产品数据格应提供与表单字段一致的悬停反馈。"""
    page = client.get("/t2").text

    assert 'class="${editable?"editable-product-cell":"readonly"}"' in page
    assert ".product-table td.editable-product-cell{cursor:pointer}" in page
    assert ".product-table td.editable-product-cell:hover{background:#cff}" in page


def test_t2_allows_editing_summary_fields_loaded_from_eportal(client):
    page = client.get("/t2").text

    assert 'const renderTotalsEditable=renderTotals' in page
    assert 'readonly=node.id==="totalAmount"||node.id==="totalRevenue"||!field||field.editable===false' in page


def test_t2_submit_shows_a_result_dialog_with_failure_reason_and_return_action(client):
    page = client.get("/t2").text

    assert 'id="submitResult"' in page
    assert 'id="submitResultMessage"' in page
    assert 'id="returnToEportal"' in page
    assert 'showSubmitResult("提交成功"' in page
    assert 'showSubmitResult("提交失败",result.message' in page
    assert 'showSubmitResult("提交失败",error.message||"提交失败")' in page


def test_t2_returns_to_the_verified_eportal_page_after_a_successful_submit(client):
    """同步并提交后回到来源 ePortal 页；不得接受任意外站 return_url。"""
    page = client.get("/t2").text

    assert "function returnToEportal()" in page
    assert "window.opener" in page
    assert "history.back()" in page
    assert 'params.get("return_url")' in page
    assert "EPORTAL_BASE_URL" in page
    assert "__EPORTAL_BASE_URL__" not in page


def test_t2_render_tolerates_a_partial_cached_page_shell(client):
    """跳转页被浏览器缓存为旧壳时，不得因可选展示节点缺失而中断整页渲染。"""
    page = client.get("/t2").text

    assert 'SO：<input id="so1"' not in page


def test_t2_keeps_the_eportal_layout_when_loading_a_remote_ticket_order(client):
    page = client.get("/t2").text

    assert "remoteOrder" in page
    assert "远端订单暂不支持保存" in page
    assert "field.type===\"select\"" in page


def test_t2_offers_saved_values_in_the_requested_dropdown_fields_and_a_calendar(client):
    """下拉字段须合并本订单历史候选；交期须使用浏览器日期控件。"""
    page = client.get("/t2").text

    assert "selectOptions=order.select_options||{}" in page
    assert 'type="date"' in page
    assert 'key==="currency"||key==="cost_currency"' in page


def test_t2_reproduces_customer_contact_and_warehouse_rules_from_eportal_source(client):
    """选客回填联系人；产品行按签约公司、GCF 和客户 ID 计算 Node ID、Warehouse。"""
    page = client.get("/t2").text

    assert '"user_contact"' in page[page.index("function applyCustomer"):]
    assert "function syncWarehouseFromSourceRules()" in page
    assert 'B:{JCSH:"FG-JCSH-BJ",JCBJ:"FG-JCBJ"}' in page
    assert 'G:{JCSH:"FG-JCSH-GZ",JCGZ:"FG-JCGZ"}' in page


def test_t2_field_aliases_resolve_local_canonical_labels(client):
    """本地订单字段名是 canonical 中文/完整英文，别名须映射到这些标签才能回显。"""
    page = client.get("/t2").text

    assert 'exchange_rate:"Exchange Rate (for foreign currency)"' in page
    assert 'sales_bundling:"Sales Bundling (Product with Service)"' in page
    assert 'product_amount:"产品含税总金额"' in page
    assert 'total_gp:"合同总GP%"' in page
