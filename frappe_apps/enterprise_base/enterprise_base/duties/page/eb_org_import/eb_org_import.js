frappe.pages['eb-org-import'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({ parent: wrapper, title: '组织任职导入', single_column: true });
    let file_url;
    $('<p>HR 先在后台建档；本页只更新任职。上传私有模板，先预检，再整批保存。</p>').appendTo(page.body);
    const company = frappe.ui.form.make_control({ parent: page.body, render_input: true,
        df: { fieldtype: 'Link', options: 'Company', fieldname: 'company', label: '公司', reqd: 1 } });
    page.add_inner_button('上传 xlsx', () => new frappe.ui.FileUploader({
        is_private: 1, allow_multiple: false, restrictions: { allowed_file_types: ['.xlsx'] },
        on_success: file => { file_url = file.file_url; frappe.show_alert('文件已上传，请预检'); }
    }));
    const run = async save => {
        if (!file_url || !company.get_value()) return frappe.msgprint('请选择公司并上传文件。');
        const result = await frappe.xcall('enterprise_base.duties.org_import.import_workbook',
            { file_url, company: company.get_value(), save });
        const message = result.errors.length ? result.errors.map(e =>
            frappe.utils.escape_html(`${e.sheet} / 第 ${e.row} 行 / ${e.column}：${e.message}`)).join('<br>') :
            `${result.saved ? '整批已保存' : '预检通过'}：部门 ${result.counts['部门']}、岗位 ${result.counts['岗位']}、员工 ${result.counts['员工任职']}。`;
        frappe.msgprint({ title: result.errors.length ? '未导入，请修正' : '导入结果', message });
    };
    page.set_primary_action('预检', () => run(0));
    page.add_inner_button('整批保存', () => run(1));
};
