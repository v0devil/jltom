def jmeter_simple_writer(filename):
    template = \
    """
    <ResultCollector guiclass="SimpleDataWriter" testclass="ResultCollector" testname="results writer"
                 enabled="true">
    <boolProp name="ResultCollector.error_logging">false</boolProp>
    <objProp>
        <name>saveConfig</name>
        <value class="SampleSaveConfiguration">
            <time>true</time>
            <latency>true</latency>
            <timestamp>true</timestamp>
            <success>true</success>
            <label>true</label>
            <code>true</code>
            <message>false</message>
            <threadName>false</threadName>
            <dataType>false</dataType>
            <encoding>false</encoding>
            <assertions>false</assertions>
            <subresults>false</subresults>
            <responseData>false</responseData>
            <samplerData>false</samplerData>
            <xml>false</xml>
            <fieldNames>true</fieldNames>
            <responseHeaders>false</responseHeaders>
            <requestHeaders>false</requestHeaders>
            <responseDataOnError>false</responseDataOnError>
            <saveAssertionResultsFailureMessage>false</saveAssertionResultsFailureMessage>
            <assertionsResultsToSave>0</assertionsResultsToSave>
            <bytes>true</bytes>
            <threadCounts>true</threadCounts>
        </value>
    </objProp>
    <stringProp name="filename">{0}</stringProp>
    <stringProp name="TestPlan.comments">Added automatically</stringProp>
    </ResultCollector>
    <hashTree/>
    """.format(filename)
    return template
