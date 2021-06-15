class Test {
    constructor(csrf) {
        this.id = $('#page-data').data('test-id');
        this.data = []
        this.requestsCountData = {}
        this.startTime = 0
        this.csrf = csrf
    }

    init(csrf)
    {
        this.refresh(csrf)
    }

    refresh(csrf) {
        $.ajax({
            url: "/analyzer/test_data/",
            type: "post",
            context: this,
            async: false,
            data: {
                test_id: this.id,
                csrfmiddlewaretoken: this.csrf
            },
            success: function (data) {
                this.setData(data);
            },
            error: function (xhr) {
                //Do Something to handle error
            }
        });
    }

    setData(data) {
        this.data = data;
        this.requestsCountData =
            data.test_action_aggregate_data.reduce((a, b) => ({
                count: a.count + b.count,
                errors: a.errors + b.errors,
            }));
        this.startTime = data.test_data[data.test_data.length - 1].timestamp;
        this.servers = Object.keys(data.server_monitoring_data)
    }

}

class TestReportElement {
    constructor(id, data) {
        this.id = id;
        this.data = data;
    }
}

class TestReportGraph extends TestReportElement {
    constructor(id, data, description, title) {
        super(id, data);
        this.title = title;
        this.description = description;
        this.chart;
    }

    show() {
        this.description['data']['json'] = this.data;
        this.description['data']['mimeType'] = 'json';
        this.description['bindto'] = `#${this.id}`;
        this.description['title'] = {
            'text': this.title
        };
        console.log('adsasdsa')
        console.log(this.description)
        this.chart = bb.generate(this.description)
    }
}

class TestHighlights extends TestReportElement {
    constructor(id, test)
    {
        super(id);
        this.test = test;
    }

    show()
    {
        $.ajax({
            url: "/analyzer/compare_highlights/",
            type: "post",
            context: this,
            data: {
                test_ids: [this.test.id],
                csrfmiddlewaretoken: this.test.csrf
            },
            success: function (response) {
                $(`#${this.id}`).html(response);
            },
            error: function (xhr) {
                //Do Something to handle error
            }
        });
    }
}

class TestReport {
    constructor(test) {
        this.test = test;
        this.charts = new Array();
    }

    init() {
        this.refresh()
    }

    refresh() {
        // Init aggregate table
        console.log(this.test)
        $("#aggregate-table").tablesorter({
            theme: 'bootstrap',
            widthFixed: true,
            showProcessing: true,
            headerTemplate: '{content} {icon}',
            widgets: ['zebra', 'stickyHeaders', 'filter'],
            widgetOptions: {
                stickyHeaders: '',
                stickyHeaders_offset: 0,
                stickyHeaders_cloneId: '-sticky',
                stickyHeaders_addResizeEvent: true,
                stickyHeaders_includeCaption: true,
                stickyHeaders_zIndex: 2,
                stickyHeaders_attachTo: null,
                stickyHeaders_xScroll: null,
                stickyHeaders_yScroll: null,
                stickyHeaders_filteredToTop: true
            }
        });
        $("#aggregate-table").trigger('tsUpdate');
        this.test.init();
        var testStartTime = this.test.startTime;
        var testHighlights = new TestHighlights('compare-highlights', this.test);
        testHighlights.show();
        this.charts = [
            // new TestReportGraph('compare-cpu-graph',
            //     this.test.data.compare_data.map(function (r) {
            //         var metric = 'cpu_load'
            //         var cData = {}
            //         cData['test_name'] = r['test_name'];
            //         r[metric].forEach
            //         (
            //             function (d, dId) {
            //                 cData[d['server__server_name'].replace(/\./g, '_')] = d[metric]
            //             }
            //         )
            //         return cData;
            //     }), {
            //         data: {
            //             keys: {
            //                 x: 'test_name',
            //                 value: this.test.servers
            //             },
            //             type: 'bar',
            //         },
            //         axis: {
            //             x: {
            //                 type: 'category'
            //             },
            //             y: {
            //                 padding: {
            //                     top: 0,
            //                     bottom: 0
            //                 },
            //                 label: '%',
            //                 max: 100,
            //                 min: 0,
            //             }
            //         },
            //         grid: {
            //             x: {
            //                 lines: [{
            //                     value: this.test.data.name,
            //                     text: 'Current test',
            //                     position: 'end'
            //                 }]
            //             }
            //         },
            //         regions: [{
            //             axis: 'x',
            //             start: 0.5,
            //             class: 'regionX'
            //         }, ],
            //     }, 'Compare graph'
            // ),
            new TestReportGraph('compare-rt-graph',
                this.test.data.compare_data, {
                    data: {
                        type: 'bar',
                        keys: {
                            x: 'test_name',
                            value: ['mean', 'median'],
                        },
                        type: 'bar',
                    },
                    axis: {
                        x: {
                            type: 'category',
                        },
                        y: {
                            padding: {
                                top: 0,
                                bottom: 0
                            },
                            label: 'ms',
                            min: 0,
                        }
                    },
                    grid: {
                        x: {
                            lines: [{
                                value: this.test.data.name,
                                text: 'Current test',
                                position: 'end'
                            }]
                        }
                    },
                    regions: [{
                        axis: 'x',
                        start: 0.5,
                        class: 'regionX'
                    }, ],
                }, 'Compare graph'
            ),
            /*new TestReportGraph('monitoring-graph',
                this.test.data.server_monitoring_data[self.test.servers[0]].map(function (r) {
                    return {
                        timestamp: new Date(r.timestamp) - new Date(testStartTime),
                        CPU_user: r['CPU_user'],
                    };
                }), {
                    data: {
                        type: 'bar',
                        keys: {
                            x: 'timestamp',
                            value: ['CPU_user'],
                        },
                        xFormat: '%H:%M:%S',
                    },
                    axis: {
                        x: {
                            type: 'timeseries',
                            tick: {
                                format: '%H:%M:%S'
                            }
                        },
                        y: {
                            max: 100,
                            min: 0,
                            padding: {
                                top: 0,
                                bottom: 0
                            },
                        },
                    },
                }, 'Monitoring graph ' + self.test.servers[0]
            ),*/
            new TestReportGraph('rtot-graph',
                this.test.data.test_data.map(function (r) {
                    return {
                        timestamp: new Date(r.timestamp) - new Date(testStartTime),
                        mean: r.mean,
                        median: r.median,
                        rps: r.count / 60
                    };
                }), {
                    data: {
                        type: 'line',
                        keys: {
                            x: 'timestamp',
                            value: ['mean', 'median', 'rps'],
                        },
                        xFormat: '%Y-%m-%dT%H:%M:%S.%LZ',
                        axes: {
                            rps: 'y2'
                        },
                    },
                    zoom: {
                        enabled: true
                    },
                    axis: {
                        x: {
                            type: 'timeseries',
                            tick: {
                                format: '%H:%M:%S'
                            }
                        },
                        y: {
                            min: 0,
                            padding: {
                                top: 0,
                                bottom: 0
                            },
                            label: 'response times (ms)',
                        },
                        y2: {
                            min: 0,
                            show: true,
                            padding: {
                                top: 0,
                                bottom: 0
                            },
                            label: 'Requests/s',
                        }

                    },
                }, 'Response times over time graph'
            ),
            new TestReportGraph('errors-graph',
                [{
                    'fail_%': this.test.requestsCountData.errors * 100 / this.test.requestsCountData.count,
                    'success_%': 100 - this.test.requestsCountData.errors * 100 / this.test.requestsCountData.count
                }], {
                    data: {
                        type: 'donut',
                        keys: {
                            value: ['fail_%', 'success_%']
                        },
                        colors: {
                            'success_%': '#A1DF6F',
                            'fail_%': '#DF6F80'
                        },
                    },
                }, 'Successful requests (%)'
            ),
            new TestReportGraph('top-mean-graph',
                d3.groups(
                    this.test.data.test_action_aggregate_data, d => d.action
                )
                .sort(function (a, b) {
                    return d3.descending(a[1][0].mean, b[1][0].mean);
                }).map(r => {
                    return {
                        action: r[0],
                        mean: r[1][0]['mean'],
                    };
                }), {
                    data: {
                        keys: {
                            x: 'action',
                            value: ['mean']
                        },
                        type: 'bar',
                    },
                    axis: {
                        x: {
                            type: 'category'
                        },
                        y: {
                            padding: {
                                top: 0,
                                bottom: 0
                            },
                            label: {
                                text: 'ms',
                                position: "outer-middle"
                            },
                            min: 0,
                        }
                    },
                    regions: [{
                        axis: 'y',
                        start: 200,
                        class: 'highRT'
                    }, ],
                },
                'Top slowest actions'
            ),
        ];

        this.charts.forEach(
            function (graph, elementId) {
                graph.show();
            }
        )

    }
}

$('a[data-toggle="tab"]').on('shown.bs.tab', function (e) {
    testReport.charts.forEach(
        function (c, chartId) {
            c.chart.resize();
        }
    )
});
