def generate_confluence_graph(
    project, data, type='bar', x_axis='name',
    exclude_metrics=['started_at'],
):
    exclude_metrics.append(x_axis)
    metric_names = []
    for k in data[0]:
        if k not in exclude_metrics:
            metric_names.append(k)
    html = ""
    html += '''
        <ac:structured-macro ac:macro-id="f16ff7f1-2469-49cb-ad18-d9ed551fe985" ac:name="chart" ac:schema-version="1">
        <ac:parameter ac:name="subTitle">Aggregate average/median response times</ac:parameter>
        <ac:parameter ac:name="xtitle">display_name</ac:parameter>
        <ac:parameter ac:name="aggregation">average,median</ac:parameter>
        <ac:parameter ac:name="type">{0}</ac:parameter>
        <ac:parameter ac:name="width">1200</ac:parameter>
        <ac:parameter ac:name="height">300</ac:parameter>
        <ac:parameter ac:name="column">display_name</ac:parameter>
        <ac:parameter ac:name="pieKeys">display_name</ac:parameter>
        <ac:parameter ac:name="minvalue">0</ac:parameter>
        <ac:parameter ac:name="yLabel">response times (ms)</ac:parameter>
        <ac:parameter ac:name="xLabel">release name</ac:parameter>
        <ac:parameter ac:name="categoryLabelPosition">up90</ac:parameter>
        <ac:rich-text-body>
        <table class="wrapped">
        <colgroup>
        <col/>
        <col/>
        </colgroup>
        <tbody>
        <tr>
        <th></th>
    '''.format(type)
    for row in reversed(data):
        html += "<th>{0}</th>".format(row['name'])
    html += "</tr>"
    for metric_name in metric_names:
        html += "<tr>"
        html += "<td>{0}</td>".format(metric_name)
        for row in reversed(data):
            html += "<td>{0}</td>".format(row[metric_name])
        html += "</tr>"
    html += '''
        </tbody>
        </table>
        </ac:rich-text-body>
        </ac:structured-macro>
    '''
    return html
