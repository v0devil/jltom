var csrf = $('#page-data').data('csrf');
var test = new Test(csrf);
var testReport = new TestReport(test);
testReport.init();

// $('a[data-toggle="tab"]').on('shown.bs.tab', function (e) {
//     localStorage.setItem('activeTab', $(e.target).attr('href'));
// });
// var activeTab = localStorage.getItem('activeTab');
// if(activeTab){
//     $('.nav-tabs a[href="' + activeTab + '"]').tabs('show');
// }
