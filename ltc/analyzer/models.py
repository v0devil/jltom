from datetime import datetime, timedelta
from django.conf import settings
from django.template.loader import render_to_string
from ltc.controller.graphite import graphiteclient
import re

from django.db import models
from django.contrib.postgres.fields import JSONField
from django.template import Template, Context

class ReportTemplate(models.Model):
    name = models.CharField(max_length=255)
    body = models.TextField(default='')
    confluence_space = models.TextField(null=True, blank=True)
    confluence_page = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def generate_report(self):
        return

    def __init__(self, *args, **kwargs):
        models.Model.__init__(self, *args, **kwargs)
        self._variables = None     # To cache graph templates

    def __str__(self):
        return self.name

    def get_variables(self, template=''):
        if not template:
            template = self.body
        variables = []
        regex = re.compile(r'\$\{(.+?)\}')
        variables += regex.findall(template)
        return variables

    def render(self, vars={}, force=False):
        from ltc.base.models import Test
        report = self.body
        if vars:
            t = Template(self.body)
            c = Context(vars)
            report = t.render(c)
        for variable in self.get_variables(report):
            if '.' in variable:
                v = variable.split('.')
                obj = vars.get(v[0])
                if not obj:
                    continue
                if hasattr(obj, v[1]):
                    mask = '${{{}.{}}}'.format(v[0], v[1])
                    value = str(getattr(obj, v[1]))
                    report = report.replace(mask, value)
            elif any(i in variable for i in ['*', '/', '+', '-']):
                i = [
                    index for index, char in enumerate(variable)
                    if char in ['*', '/', '+', '-']
                ]
                s = variable[i[0]]
                v = variable.split(s)
                test = vars.get('test')
                if ':' in v[1]:
                    v_ = v[1].split(':')
                    test_id = int(v_[1])
                    test = Test.objects.get(id=test_id)
                    v[1] = v_[0]
                if v:
                    mask = '${{{}}}'.format(variable)
                    template = test.project.template
                    v1 = GraphiteVariable.objects.filter(
                        template=template,
                        name=v[0].strip(),
                    ).first()
                    v2 = GraphiteVariable.objects.filter(
                        template=template,
                        name=v[1].strip(),
                    ).first()
                    if not v1 or not v2:
                        continue
                    v1_value, c = ReportCache.objects.get_or_create(
                        test=test, name=v1.name
                    )
                    if c or not v1_value.value:
                        v1.render(test, force=force)
                    v2_value, c = ReportCache.objects.get_or_create(
                        test=test, name=v2.name
                    )
                    if c or not v2_value.value:
                        v2.render(test, force=force)
                    v1_value = float(v1_value.value)
                    print(v2_value)
                    print(v2_value.value)
                    v2_value = float(v2_value.value)
                    res = 0.0
                    if s == '*':
                        res = v1_value * v2_value
                    if s == '/':
                        res = v1_value / v2_value
                    if s == '+':
                        res = v1_value + v2_value
                    if s == '-':
                        res = v1_value - v2_value
                    res = str(float('{:.2f}'.format(res)))
                    report = report.replace(
                        mask, res
                    )
            elif ':' in variable:
                v = variable.split(':')
                mask = '${{{}}}'.format(variable)
                id = int(v[1])
                test = Test.objects.get(id=id)
                template = test.project.template
                var = GraphiteVariable.objects.filter(
                    template=template,
                    name=v[0].strip(),
                ).first()
                if var:
                    report = report.replace(
                        mask,  var.render(test, force=force)
                    )
            elif variable == 'aggregate_table':
                mask = '${{{}}}'.format(variable)
                report = report.replace(
                    mask, self.generate_aggregate_table(vars.get('test'))
                )
            else:
                v = self.variables.filter(name=variable).first()
                if v:
                    mask = '${{{}}}'.format(variable)
                    report = report.replace(
                        mask, v.render(vars.get('test'), force=force)
                    )
        return report

    def generate_aggregate_table(self, test):
        content = ''
        aggregate_table = test.aggregate_table()
        aggregate_table_html = render_to_string(
            'confluence/aggregate_table.html',
            {'aggregate_table': aggregate_table}
        )
        content += "<h2>Aggregate table</h2>"
        content += aggregate_table_html
        return content



class GraphiteVariable(models.Model):
    GRAPH = 'G'
    VALUE = 'V'

    TYPES = (
        (GRAPH, 'graph'),
        (VALUE, 'value'),
    )

    AVG = 'A'
    MAX = 'MA'
    MIN = 'MI'

    FUNCTIONS = (
        (AVG, 'avg'),
        (MAX, 'max'),
        (MIN, 'min')
    )

    template = models.ForeignKey(
        ReportTemplate, on_delete=models.CASCADE, related_name='variables'
    )
    name = models.CharField(max_length=255)
    variable_type = models.CharField(
        max_length=12, choices=TYPES, default=GRAPH
    )
    function = models.CharField(
        max_length=12, choices=FUNCTIONS, default=AVG
    )
    period = models.CharField(
        max_length=12, default='', blank=True
    )
    query = models.TextField(blank=True)
    description = models.TextField(blank=True)
    rampup_include = models.BooleanField(default=True)
    max_value = models.FloatField(blank=True, null=True)
    min_value = models.FloatField(blank=True, null=True)
    class Meta:
        unique_together = [['template', 'name']]

    def __str__(self):
        return self.name

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._gc = graphiteclient.GraphiteClient(
            settings.GRAPHITE_URL,
            settings.GRAPHITE_USER,
            settings.GRAPHITE_PASSWORD,
        )

    def render_graph(self, test, data):
        # Store min and max row values for better scaling.
        chart_values_max = None
        chart_values_min = None

        # Build an simple html table
        chart_table = '<table class="wrapped"><tbody>'

        # Table header
        chart_table += '<tr><th>Date</th>'
        for key, value in sorted(data.items()):
            chart_table += '<th>' + key + '</th>'
        chart_table += '</tr>\n'

        # chart rows
        while len(data[key]) > 0:
            timestamp = data[key][0][1]
            chart_date = datetime.fromtimestamp(
                timestamp
            ).strftime('%Y-%m-%d %H:%M')
            chart_table += '<tr><td>' + chart_date + '</td>'

            for key, value in sorted(data.items()):
                val = value.pop(0)[0]
                if val:
                    this_row = round(val, 3)
                else:
                    this_row = 0.0

                chart_table += '<td>' + str(this_row) + '</td>'

                # Find min/max values.
                if val:
                    if not chart_values_max or chart_values_max < val:
                        chart_values_max = val

                    if not chart_values_min or chart_values_min > val:
                        chart_values_min = val

            chart_table += '</tr>\n'

        # char footer
        chart_table += '</tbody></table>\n'

        range_axis_lower_bound = chart_values_min * 0.95
        if self.min_value is not None:
            range_axis_lower_bound = self.min_value
        range_axis_upper_bound = chart_values_max * 1.05
        if self.max_value is not None:
            range_axis_upper_bound = self.max_value

        chart_params = {
            'type': 'xyLine',
            'subTitle': self.name,
            'width': 1200,
            'height': 500,
            'rangeAxisLowerBound': range_axis_lower_bound,
            'rangeAxisUpperBound': range_axis_upper_bound,
            'dateFormat': 'yyyy-MM-dd HH:mm',
            'timePeriod': 'Minute',
            'legend': 'true',
            'dataOrientation': 'vertical',
            'timeSeries': 'true',
            'tables': '1,2',
            'showShapes': 'false',
        }

        chart_acparams = ''
        for key, value in list(chart_params.items()):
            chart_acparams += (
                '<ac:parameter ac:name="{}">{}</ac:parameter>\n').format(
                key, value)
        res = (
            '<ac:structured-macro ac:macro-id="" ac:name="chart" ac:schema-version="1">'
            f'{chart_acparams}'
            '<ac:rich-text-body>'
            '<p class="auto-cursor-target">'
            '<br/>'
            '</p>'
            f'{chart_table}'
            '<p class="auto-cursor-target">'
            '<br/>'
            '</p>'
            '</ac:rich-text-body>'
            '</ac:structured-macro>'
        )
        return res

    def render_value(self, test, data):
        l = []
        for k, v in data.items():
            for v_ in v:
                if not v_[0]:
                    continue
                l.append(v_[0])
        res = 0.0
        if self.function == GraphiteVariable.AVG:
            if len(l) > 0:
                res = sum(l) / len(l)
        elif self.function == GraphiteVariable.MIN:
            res = min(l)
        elif self.function == GraphiteVariable.MAX:
            res = max(l)
        return str(float('{:.2f}'.format(res)))

    def render(self, test, force=False):
        print(self)
        print('a')
        cached_value = ReportCache.objects.filter(
            name=self.name,
            test=test
        )
        print('b')
        if not force and cached_value.exists():
            print('b3')
            print(force)
            print(cached_value.exists())
            return cached_value.first().value
        print('b2')
        started_at = test.started_at
        finished_at = test.finished_at
        if not self.rampup_include:
            started_at = finished_at - timedelta(
                seconds=test.duration
            )
        print('c')
        ts_start = started_at.strftime("%H:%M_%Y%m%d")
        ts_end = finished_at.strftime("%H:%M_%Y%m%d")
        if self.period:
            ts_start = self.period
            ts_end = 'now'

        # Exclude test rampup period from analyzed data

        results = self._gc.query(self.query, ts_start, ts_end, 'json')
        if sum([
            x['datapoints'][-1][0] for x in results
            if x['datapoints'][-1][0]
        ]
        ) == 0:
            for row in results:
                del(row['datapoints'][-1])

        # Copy results into a new dictionary with data source as key.
        data = {}
        for item in results:
            target = item['target']
            data[target] = item['datapoints']
        value = ''
        if self.variable_type == GraphiteVariable.GRAPH:
            value = self.render_graph(test, data)
            print('hui')
            print(value)
        if self.variable_type == GraphiteVariable.VALUE:
            value = self.render_value(test, data)
            print('dadsa')
            print(value)

        ReportCache.objects.update_or_create(
                test=test,
                name=self.name,
                defaults={
                    'value': value
                }
            )
        return value


class ReportCache(models.Model):
    test = models.ForeignKey(to='base.Test', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    value = models.TextField(blank=True)

    class Meta:
        unique_together = (('test', 'name'))

class TestDataResolution(models.Model):
    frequency = models.CharField(max_length=100)
    per_sec_divider = models.IntegerField(default=60)

    def __str__(self):
        return self.frequency

class TestData(models.Model):
    test = models.ForeignKey(to='base.Test', on_delete=models.CASCADE)
    data_resolution = models.ForeignKey(
        TestDataResolution, on_delete=models.CASCADE, default=1
    )
    source = models.CharField(max_length=100, default='default')
    data = JSONField()


class Action(models.Model):
    name = models.TextField()
    project = models.ForeignKey(
        to='base.Project', on_delete=models.CASCADE, default=1)
    description = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = (('name', 'project'))


class Error(models.Model):
    text = models.TextField(db_index=True)
    code = models.CharField(max_length=400, null=True, blank=True)


class TestError(models.Model):
    test = models.ForeignKey(to='base.Test', on_delete=models.CASCADE)
    action = models.ForeignKey(Action, on_delete=models.CASCADE)
    error = models.ForeignKey(Error, on_delete=models.CASCADE)
    count = models.IntegerField(default=0)


class TestActionData(models.Model):
    test = models.ForeignKey(to='base.Test', on_delete=models.CASCADE)
    data_resolution = models.ForeignKey(
        TestDataResolution, on_delete=models.CASCADE,
        default=1
    )
    action = models.ForeignKey(
        Action, on_delete=models.CASCADE, null=True, blank=True
    )
    data = JSONField()

    class Meta:
        index_together = [
            ('test', 'action', 'data_resolution'),
        ]


class TestActionAggregateData(models.Model):
    test = models.ForeignKey(to='base.Test', on_delete=models.CASCADE)
    action = models.ForeignKey(Action, on_delete=models.CASCADE)
    data = JSONField()

    class Meta:
        index_together = [
            ('test', 'action'),
        ]


class Server(models.Model):
    server_name = models.TextField()
    description = models.TextField()


class ServerMonitoringData(models.Model):
    test = models.ForeignKey(to='base.Test', on_delete=models.CASCADE)
    data_resolution = models.ForeignKey(
        TestDataResolution, on_delete=models.CASCADE, default=1
    )
    source = models.TextField(default='default')
    server = models.ForeignKey(Server, on_delete=models.CASCADE)
    data = JSONField()

    class Meta:
        index_together = [
            ('test', 'server', 'source', 'data_resolution'),
        ]
