class Dashboard {

    constructor() {
        this.loadGenerators = new LoadGenerators('load-generators');
        this.activeTests = new activeTests('active-tests');
        this.refreshInterval = 10000;
    }

    init() {
        this.refresh();
        var d = this;
        setInterval(function () {
            d.refresh();
        }, this.refreshInterval);

    }

    refresh() {
        this.loadGenerators.refresh();
        this.activeTests.refresh();
    }
}

class DashboardElement {

    constructor(id) {
        this.id = id
        this.html = ''
    }

    show() {
        if ($(`#${this.id}`).length) {
            $(`#${this.id}`).empty();
            $(`#${this.id}`).append(this.html);
        }
    }
}

class LoadGenerators extends DashboardElement {
    constructor(id, html) {
        super(id, html);
    }

    init() {
        this.refresh();
    }

    refresh() {
        $.ajax({
            url: '/api/v1/loadgenerator/',
            async: 'true',
            type: 'get',
            context: this,
            success: function (response) {
                let html =
                    `<table class="table">
                <thead>
                <tr>
                <th>Host</th>
                <th>Free memory</th>
                <th>VCPUs</th>
                <th>la_5</th>
                <th>Jmeter Instances</th>
                <th>Status</th>
                </tr>
                </thead>`;
                $.each(response, function (i, loadgenerator) {
                    var jmeter_servers_count = loadgenerator['jmeter_servers_count'];
                    var jmeter_servers_label = "info"
                    var jmeter_servers_count = loadgenerator.jmeter_servers.length
                    if (jmeter_servers_count >= 5) jmeter_servers_label = "danger";
                    else if (jmeter_servers_count < 5 && jmeter_servers_count > 2)
                    jmeter_servers_label = "warning";
                    var load = `${loadgenerator['la_1']}/${loadgenerator['la_5']}/${loadgenerator['la_15']}`;
                    var jmeter_servers_label = 'info'
                    if (jmeter_servers_count >= 5) jmeter_servers_label = 'danger';
                    else if (jmeter_servers_count < 5 && jmeter_servers_count > 2) jmeter_servers_label = 'warning';
                    html += 
                    `<tr>
                        <td><a href="/controller/load_generator/get_data/${loadgenerator['id']}"
                            onclick="return popitup(\'/controller/load_generator/get_data/${loadgenerator['id']}\')">${loadgenerator['hostname']}</a></td>
                        <td>${loadgenerator['memory_free']}</td>
                        <td>${loadgenerator['num_cpu']}</td>
                        <td>${load}</td>
                        <td><span class="badge badge-pill badge-${jmeter_servers_label}">${jmeter_servers_count}</span></td>
                        <td><span class="badge badge-pill badge-success">ok</span></td>
                    </tr>`;
                });
                html += '</table>';
                this.html = html;
                this.show();
            },
            error: function (xhr) {}
        });
    }
}
class activeTests extends DashboardElement {
    constructor(id, html) {
        super(id, html);
    }

    init() {
        this.refresh();
    }

    refresh() {
        $.ajax({
            url: '/api/v1/test/?status[]=R&status[]=A',
            async: 'true',
            type: 'get',
            context: this,
            success: function (response) {
                if(response.length == 0) {
                    this.html = '<b>No active tests</b>'
                }
                else
                {
                    let html =
                    `<table class="table">
                        <thead>
                            <tr>
                            <th>Project</th>
                            <th>TestID</th>
                            <th>Progress</th>
                            <th>Status</th>
                            </tr>
                        </thead>`
                    $.each(response, function (i, test) {
                        var start_time = test['start_time'];
                        var current_time = test['current_time'];
                        var duration = test['duration'];
                        var progress = Math.round(((current_time - start_time) / 1000) * 100 / duration)
                        if (progress > 100) progress = 100;
                        var progress_bar =
                        `<div class="progress">
                        <div class="progress-bar" role="progressbar" style="width: ${progress}%" aria-valuenow="${progress}" aria-valuemin="0"
                            aria-valuemax="100" style="min-width: 2em;">${progress}%</div>
                        </div>`
                        html += `<tr><td>${test?.project?.name}</td><td>${test['id']}</td><td>${progress_bar}</td><td>${test['status']}</td></tr>`;
                    });
                    html += '</table>';
                    this.html = html;
                }
                this.show();
            },
            error: function (xhr) {}
        });
    }
}
var dashboard = new Dashboard();
dashboard.init();
