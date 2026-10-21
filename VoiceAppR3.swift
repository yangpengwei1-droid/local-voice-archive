import Cocoa
import WebKit
import Speech
import AVFoundation
import CoreAudio
import AudioToolbox

// Aggregate levels only. Never retain audio samples or write microphone buffers.
final class MicrophoneStats {
    let lock=NSLock()
    var buffers:Int64=0,frames:Int64=0,nonzeroFrames:Int64=0
    var level:Double = -120,peak:Double = -120,lastVoice:TimeInterval=0
    var appendEnabled=true
    func reset(){lock.lock();defer{lock.unlock()};buffers=0;frames=0;nonzeroFrames=0;level = -120;peak = -120;lastVoice=0;appendEnabled=true}
    func closeInput(_ request:SFSpeechAudioBufferRecognitionRequest?){lock.lock();appendEnabled=false;request?.endAudio();lock.unlock()}
    func consume(_ buffer:AVAudioPCMBuffer,_ request:SFSpeechAudioBufferRecognitionRequest)->Bool {
        var sum:Double=0,maxValue:Double=0,count=0,nonzero=0
        if let data=buffer.floatChannelData {
            for ch in 0..<Int(buffer.format.channelCount) {for i in 0..<Int(buffer.frameLength){let v=Double(data[ch][i]);sum+=v*v;maxValue=max(maxValue,abs(v));count+=1;if abs(v)>0.00001 {nonzero+=1}}}
        }
        let db=count>0 ? max(-120,20*log10(max(0.000001,sqrt(sum/Double(count))))) : -120
        lock.lock();defer{lock.unlock()};buffers+=1;frames+=Int64(buffer.frameLength);nonzeroFrames+=Int64(nonzero);level=db;peak=max(peak,maxValue>0 ? 20*log10(maxValue) : -120)
        if db > -42 {lastVoice=Date().timeIntervalSince1970};if appendEnabled {request.append(buffer)};return appendEnabled
    }
    func snapshot()->[String:Any]{lock.lock();defer{lock.unlock()};return ["buffers":buffers,"frames":frames,"nonzero_samples":nonzeroFrames,"dbfs":level,"peak_dbfs":peak,"last_voice_unix":lastVoice]}
}

struct DiagnosticSession {
    enum Phase:String {case idle,authorizing,capturing,awaiting,completed,cancelled}
    var phase=Phase.idle
    var id=""
    var started:TimeInterval?
    var partial=""
    var busy:Bool {phase == .authorizing || phase == .capturing || phase == .awaiting}
    mutating func begin()->Bool {guard !busy else{return false};phase = .authorizing;id=UUID().uuidString;started=nil;partial="";return true}
    mutating func engineStarted(_ now:TimeInterval)->Bool {guard phase == .authorizing else{return false};started=now;phase = .capturing;return true}
    func remaining(_ now:TimeInterval)->Int {guard let t=started else{return 10};return max(0,Int(ceil(10-(now-t))))}
    mutating func ending()->Bool {guard phase == .capturing else{return false};phase = .awaiting;return true}
    mutating func record(_ text:String){if busy && !text.isEmpty {partial=text}}
    mutating func end(_ cancelled:Bool){phase = cancelled ? .cancelled : .completed}
}

final class ArchiveApp: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    let engine = AVAudioEngine()
    let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
    var request: SFSpeechAudioBufferRecognitionRequest?
    var task: SFSpeechRecognitionTask?
    var fileRequest: SFSpeechURLRecognitionRequest?
    var testAudioFiles: [URL] = []
    var testAudioIndex = 0
    var consecutiveSilences = 0
    var recoveryCount = 0
    var inputEndReason = ""
    var authorizationTimer: Timer?
    var authorizationPending = false
    let traceQueue = DispatchQueue(label:"local.archive.voice.trace",qos:.utility)
    var timer: Timer?
    var sessionTimer: Timer?
    var finalWaitTimer: Timer?
    var awaitingFinal = false
    var active = false
    var singleUtterance = false
    var generation = 0
    var clientEpoch = -1
    var pending = ""
    var lastNonemptyPartial = ""
    let microphoneStats=MicrophoneStats()
    var meterTimer:Timer?
    var captureStarted:TimeInterval=0
    var firstResultLogged=false
    var noFramesReported=false
    var lastMeterLog:TimeInterval=0
    var diagnosticOnly=false
    var diagnosticSession=DiagnosticSession()
    var captureInfo:[String:Any]=[:]

    var hasTap = false
    var contextualTerms: [String] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let i=CommandLine.arguments.firstIndex(of:"--local-audio-test-manifest"), CommandLine.arguments.count>i+1,
           let raw=try? Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[i+1])),
           let paths=(try? JSONSerialization.jsonObject(with:raw)) as? [String] {
            let allowed=Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("var/audio-tests").standardizedFileURL.path+"/"
            let urls=paths.map { URL(fileURLWithPath:$0).resolvingSymlinksInPath() }
            if !urls.isEmpty && urls.allSatisfy({$0.path.hasPrefix(allowed)}) {testAudioFiles=urls}
        }
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        let reloadItem = NSMenuItem(title:"停止监听并刷新工作台",action:#selector(reloadWorkbench),keyEquivalent:"r")
        reloadItem.target = self
        appMenu.addItem(reloadItem)
        appMenu.addItem(NSMenuItem(title:"退出",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q"))
        appItem.submenu = appMenu
        menu.addItem(appItem)
        NSApp.mainMenu = menu
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "voice")
        web = WKWebView(frame: .zero, configuration: config)
        web.uiDelegate = self
        web.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0,y: 0,width: 1160,height: 850),styleMask: [.titled,.closable,.miniaturizable,.resizable],backing: .buffered,defer: false)
        window.title = "档案助手 · 本地合同工作台（未同步）"
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        web.load(URLRequest(url: URL(string:"http://127.0.0.1:8877")!))
        NSApp.activate(ignoringOtherApps: true)
        NotificationCenter.default.addObserver(self,selector:#selector(audioConfigurationChanged),name:.AVAudioEngineConfigurationChange,object:engine)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.evaluateJavaScript("window.nativeSpeechContextsSupported=true;window.nativeReady&&window.nativeReady({build:'public-alpha-1',locale:'zh-CN',on_device:true,open_workfile:true,audio_file_test:\(!testAudioFiles.isEmpty)})",completionHandler:nil)
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel(); panel.allowsMultipleSelection = false; panel.canChooseDirectories = false; panel.allowedFileTypes = ["xlsx"]
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.addButton(withTitle:"确认"); alert.addButton(withTitle:"取消")
        alert.beginSheetModal(for: window) { response in completionHandler(response == .alertFirstButtonReturn) }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        stop("window_reopen"); event("status","窗口已恢复，麦克风保持关闭；合同选择保留，可点击恢复连续监听。",reason:"window_reopen"); window.makeKeyAndOrderFront(nil); return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { stop("application_terminate") }
    @objc func reloadWorkbench() {
        web.evaluateJavaScript("window.prepareWorkbenchReload ? window.prepareWorkbenchReload() : false") { result,_ in
            guard result as? Bool == true else { return }
            self.stop("workbench_reload"); self.web.reload()
        }
    }
    @objc func audioConfigurationChanged() {
        DispatchQueue.main.async {
            guard self.active else { return }
            self.recover("audio_configuration_changed","音频设备配置发生变化")
        }
    }
    func trace(_ name:String,_ detail:String="",source:String="native",eventID:String="") {
        let value:[String:Any]=["source":source,"command_event_id":eventID,"diagnostic_id":diagnosticSession.id,"diagnostic_phase":diagnosticSession.phase.rawValue,"pid":ProcessInfo.processInfo.processIdentifier,"bundle_id":Bundle.main.bundleIdentifier ?? "unknown","at":ISO8601DateFormatter().string(from:Date()),"event":name,"detail":detail,"mode":singleUtterance ? "once" : "continuous","active":active,"generation":generation,"epoch":clientEpoch,"pending_chars":pending.count]
        traceQueue.async {
        let folder=Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("var/runtime")
        let url=folder.appendingPathComponent("native_voice.jsonl")
        guard let data=try? JSONSerialization.data(withJSONObject:value) else{return}
        try? FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        if let n=(try? FileManager.default.attributesOfItem(atPath:url.path)[.size]) as? NSNumber,n.intValue>2097152 {try? FileManager.default.removeItem(at:folder.appendingPathComponent("native_voice.previous.jsonl"));try? FileManager.default.moveItem(at:url,to:folder.appendingPathComponent("native_voice.previous.jsonl"))}
        if !FileManager.default.fileExists(atPath:url.path){FileManager.default.createFile(atPath:url.path,contents:nil)}
        if let h=try? FileHandle(forWritingTo:url){h.seekToEndOfFile();h.write(data);h.write(Data([10]));try? h.close()}
        }
    }

    func recover(_ reason:String,_ message:String) {
        guard active else{return}
        if singleUtterance || recoveryCount>=3 {
            stop(reason+"_paused");event("error",message+"；监听已暂停（最多自动恢复3次）。合同保留，点击恢复连续监听重试。",reason:reason);return
        }
        recoveryCount+=1;reset();let token=generation
        trace("recovery",reason)
        event("status",message+"；正在自动恢复（\(recoveryCount)/3），可随时停止。",reason:reason)
        DispatchQueue.main.asyncAfter(deadline:.now()+Double(recoveryCount)) {if self.active && self.generation==token {self.listen()}}
    }
    func event(_ type: String, _ text: String, recognitionFinal: Bool? = nil, reason: String? = nil) {
        if diagnosticOnly {publishDiagnostic()}
        trace(type,["partial","final"].contains(type) ? (reason ?? "transcript_event") : (reason ?? text))
        var value: [String:Any] = ["type":type,"text":text,"epoch":clientEpoch,"mode":singleUtterance ? "once" : "continuous","active":active,"generation":generation]
        if !testAudioFiles.isEmpty { value["source"]="synthetic_audio_file";value["sample_index"]=testAudioIndex } else {value["source"]="live_microphone"}
        if let final=recognitionFinal { value["recognition_final"]=final }
        if let reason=reason { value["finish_reason"]=reason }
        if let data = try? JSONSerialization.data(withJSONObject:value), let json = String(data:data,encoding:.utf8) {
            web.evaluateJavaScript("window.voiceEvent && window.voiceEvent(\(json))",completionHandler:nil)
        }
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.request.url?.host == "127.0.0.1" else { return }
        if let body=message.body as? [String:Any], body["command"] as? String == "open_workfile" {
            stop("open_workfile")
            let id=body["request_id"] as? String ?? ""
            guard let path=body["path"] as? String else { openResult(id,"",false,"缺少工作文件路径");return }
            let url=URL(fileURLWithPath:path).standardizedFileURL
            guard url.pathExtension.lowercased()=="xlsx",FileManager.default.isReadableFile(atPath:url.path) else {openResult(id,url.path,false,"文件不存在或没有读取权限");return}
            if let appURL=NSWorkspace.shared.urlForApplication(withBundleIdentifier:"com.kingsoft.wpsoffice.mac") {
                let configuration=NSWorkspace.OpenConfiguration();configuration.activates=true
                NSWorkspace.shared.open([url],withApplicationAt:appURL,configuration:configuration) { app,error in
                    DispatchQueue.main.async {self.openResult(id,url.path,error==nil && app != nil,error?.localizedDescription ?? "WPS未接受打开请求",application:"WPS Office")}
                }
            } else {let ok=NSWorkspace.shared.open(url);openResult(id,url.path,ok,"没有可用的本地表格应用")}
            return
        }
        if let body = message.body as? [String:Any], body["command"] as? String == "choose_workfile" {
            stop("choose_workfile")
            let panel = NSOpenPanel()
            panel.allowsMultipleSelection = false; panel.canChooseDirectories = false; panel.allowedFileTypes = ["xlsx"]
            panel.message = "请选择独立的本地xlsx工作副本"
            let ticket = body["epoch"] as? Int ?? -1
            let year = body["year"] as? Int ?? 0
            panel.beginSheetModal(for: window) { result in
                guard result == .OK, let path = panel.url?.path else { return }
                if let raw = try? JSONSerialization.data(withJSONObject:["path":path,"epoch":ticket,"year":year]), let json = String(data:raw,encoding:.utf8) {
                    self.web.evaluateJavaScript("window.workfileChosen && window.workfileChosen(\(json))",completionHandler:nil)
                }
            }
            return
        }
        if let body = message.body as? [String:Any], body["command"] as? String == "contexts", let terms = body["terms"] as? [String] {
            let commands=["已询价","写入已询价","已比选","写入已比选","结案通过","这份是结案报告","合同已返还","合同已返回","合同已被取走","签订日期","停止监听"]
            var seen=Set<String>()
            contextualTerms=Array((commands+terms).filter { !$0.isEmpty && $0.count<=60 && seen.insert($0).inserted }.prefix(100))
            request?.contextualStrings = contextualTerms
            if active && pending.isEmpty && !awaitingFinal && testAudioFiles.isEmpty {listen()}
            return
        }
        if let body=message.body as? [String:Any], body["command"] as? String == "epoch", let next=body["epoch"] as? Int {
            if next != clientEpoch {
                let wasListening=active || authorizationPending
                let resume=active && !singleUtterance
                stop("selection_epoch_changed"); clientEpoch=next
                if resume {active=true;listen()} else if wasListening {event("status","合同选择已改变，本句识别已取消，麦克风已关闭；当前合同可重新口述。",reason:"selection_changed_cancelled")}
            }
            return
        }
        if (message.body as? String) == "finish" { endInput("manual_end"); return }
        if (message.body as? String) == "reload" { reloadWorkbench(); return }
        if (message.body as? String) == "stop" || (message.body as? [String:Any])?["command"] as? String == "stop" {
            let body=message.body as? [String:Any]
            let reason=body?["reason"] as? String ?? "app_pause"
            stop(reason,source:body?["source"] as? String ?? "legacy_client_stop",eventID:body?["event_id"] as? String ?? "");event("status",reason == "user_stop" ? "用户已停止，麦克风已关闭；合同与候选保留。" : "应用已暂停监听，麦克风已关闭；合同与候选保留。",reason:reason);return
        }
        let details=message.body as? [String:Any]
        let command=(message.body as? String) ?? details?["command"] as? String
        if ["start","once","diagnose"].contains(command ?? "") && diagnosticSession.busy {trace("duplicate_start_ignored","command=\(command ?? "")",source:"native_diagnostic_guard");publishDiagnostic();return}
        guard command == "start" || command == "once" || command == "diagnose", !active else { return }
        guard !authorizationPending else {event("status","正在等待本次系统授权结果，请勿重复点击；可停止等待。",reason:"authorization_pending");return}
        clientEpoch=details?["epoch"] as? Int ?? -1
        singleUtterance = command == "once" || command == "diagnose"
        diagnosticOnly = command == "diagnose"
        recoveryCount=0;trace("start_command",command ?? "")
        consecutiveSilences=0
        guard let rec = recognizer, rec.supportsOnDeviceRecognition else {
            event("error","当前系统不支持中文设备端识别，请先检查系统语音语言资源；未启用联网识别。")
            return
        }
        guard rec.isAvailable else { event("error","本机中文识别当前不可用，麦克风未开启。请稍后点击开启连续语音重试。"); return }
        if diagnosticOnly {_ = diagnosticSession.begin()}
        event("status","等待语音识别授权；请查看系统弹窗，30秒未返回会暂停等待。",reason:"speech_authorization_wait")
        generation += 1
        let authorizationToken = generation
        authorizationPending=true
        authorizationTimer=Timer.scheduledTimer(withTimeInterval:30,repeats:false) { _ in
            guard self.authorizationPending,self.generation==authorizationToken else{return}
            self.stop("authorization_timeout")
            self.event("error","系统授权30秒未返回，已停止本次等待，麦克风未开启。查看系统授权后可手动重试；合同与候选保持。",reason:"authorization_timeout")
        }
        func speechResult(_ status:SFSpeechRecognizerAuthorizationStatus) {
            DispatchQueue.main.async {
                guard self.generation == authorizationToken,self.authorizationPending else {self.trace("stale_authorization_ignored");return}
                self.trace("speech_authorization_result",String(status.rawValue))
                guard status == .authorized else {
                    self.stop("speech_authorization_\(status.rawValue)")
                    self.event("error","语音识别未授权（状态\(status.rawValue)），未开启麦克风。请在系统设置核对本App权限后手动重试。",reason:"speech_permission_denied");return
                }
                func begin(_ allowed:Bool) {
                    DispatchQueue.main.async {
                        guard self.generation == authorizationToken,self.authorizationPending else {self.trace("stale_microphone_authorization_ignored");return}
                        self.authorizationTimer?.invalidate();self.authorizationTimer=nil;self.authorizationPending=false
                        guard allowed else {self.stop("microphone_permission_denied");self.event("error","麦克风未授权；请在系统设置核对权限后手动重试。",reason:"microphone_permission_denied");return}
                        self.active=true;self.listen()
                    }
                }
                // File-only ASR never opens the microphone; no microphone request needed.
                if !self.testAudioFiles.isEmpty {begin(true);return}
                switch AVCaptureDevice.authorizationStatus(for:.audio) {
                case .authorized:begin(true)
                case .notDetermined:
                    self.event("status","等待麦克风授权；可停止等待，合同保持。",reason:"microphone_authorization_wait")
                    AVCaptureDevice.requestAccess(for:.audio,completionHandler:begin)
                default:begin(false)
                }
            }
        }
        let existing=SFSpeechRecognizer.authorizationStatus()
        if existing == .notDetermined {SFSpeechRecognizer.requestAuthorization(speechResult)} else {speechResult(existing)}
    }

    func publishDiagnostic(){
        let state:[String:Any]=["phase":diagnosticSession.phase.rawValue,"id":diagnosticSession.id,"remaining_seconds":diagnosticSession.remaining(Date().timeIntervalSince1970)]
        if let data=try? JSONSerialization.data(withJSONObject:state),let json=String(data:data,encoding:.utf8){web.evaluateJavaScript("window.diagnosticLifecycle&&window.diagnosticLifecycle(\(json))",completionHandler:nil)}
    }
    func inputDeviceName()->String {
        var device=AudioDeviceID(0),size=UInt32(MemoryLayout<AudioDeviceID>.size)
        if let unit=engine.inputNode.audioUnit {AudioUnitGetProperty(unit,kAudioOutputUnitProperty_CurrentDevice,kAudioUnitScope_Global,0,&device,&size)}
        if device==0 {var address=AudioObjectPropertyAddress(mSelector:kAudioHardwarePropertyDefaultInputDevice,mScope:kAudioObjectPropertyScopeGlobal,mElement:kAudioObjectPropertyElementMain);AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),&address,0,nil,&size,&device)}
        var name:Unmanaged<CFString>?
        var address=AudioObjectPropertyAddress(mSelector:kAudioObjectPropertyName,mScope:kAudioObjectPropertyScopeGlobal,mElement:kAudioObjectPropertyElementMain);size=UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let status=withUnsafeMutablePointer(to:&name) {AudioObjectGetPropertyData(device,&address,0,nil,&size,UnsafeMutableRawPointer($0))}
        let label=status==noErr ? (name?.takeRetainedValue() as String? ?? "未知输入设备") : "设备名读取失败（\(status)）"
        return "\(label)（设备ID \(device)）"
    }
    func meter(_ token:Int) {
        guard active,generation==token else{return}
        if diagnosticOnly {publishDiagnostic()}
        let stats=microphoneStats.snapshot(),now=Date().timeIntervalSince1970
        let age=now-captureStarted,frames=stats["frames"] as? Int64 ?? 0,db=stats["dbfs"] as? Double ?? -120
        var value=captureInfo;value.merge(stats){_,new in new};value["seconds"]=age;value["phase"]=awaitingFinal ? "等待最终转写" : "等待说话 / 正在采音";value["engine_running"]=engine.isRunning
        if let data=try? JSONSerialization.data(withJSONObject:value),let json=String(data:data,encoding:.utf8){web.evaluateJavaScript("window.microphoneDiagnostic&&window.microphoneDiagnostic(\(json))",completionHandler:nil)}
        if now-lastMeterLog>=2 {lastMeterLog=now;trace("microphone_stats",String(data:try! JSONSerialization.data(withJSONObject:value,options:.sortedKeys),encoding:.utf8) ?? "")}
        if age>3 && frames==0 && !noFramesReported {noFramesReported=true;event("status","麦克风已开启但尚未收到音频帧；请查看输入设备和系统音量，当前不能判断为没有说话。",reason:"no_audio_frames")}
        if !awaitingFinal && !pending.isEmpty && !diagnosticOnly {
            let last=stats["last_voice_unix"] as? Double ?? 0
            if last>0 && now-last>=2.2 && db < -42 {endInput("audio_silence_2.2s")}
        }
    }
    func openResult(_ id:String,_ path:String,_ ok:Bool,_ error:String,application:String="本机表格应用") {
        let value:[String:Any]=["request_id":id,"path":path,"ok":ok,"error":ok ? "" : error,"application":application]
        if let raw=try? JSONSerialization.data(withJSONObject:value),let json=String(data:raw,encoding:.utf8) {web.evaluateJavaScript("window.workfileOpenResult&&window.workfileOpenResult(\(json))",completionHandler:nil)}
    }
    func reset() {
        generation += 1
        meterTimer?.invalidate();meterTimer=nil
        finalWaitTimer?.invalidate(); finalWaitTimer=nil; awaitingFinal=false
        timer?.invalidate(); timer = nil
        sessionTimer?.invalidate(); sessionTimer = nil
        microphoneStats.closeInput(request)
        engine.stop()
        if hasTap { engine.inputNode.removeTap(onBus:0); hasTap = false }
        request?.endAudio(); task?.cancel()
        request = nil; fileRequest=nil; task = nil; pending = "";lastNonemptyPartial=""
    }
    func stop(_ reason:String="internal_stop",source:String="native_lifecycle",eventID:String="") {trace("stop",reason,source:source,eventID:eventID);if diagnosticOnly {diagnosticSession.end(!reason.hasPrefix("diagnostic_complete") && reason != "diagnostic_asr_error");publishDiagnostic()}; authorizationTimer?.invalidate();authorizationTimer=nil;authorizationPending=false;active = false; reset();web?.evaluateJavaScript("window.microphoneStopped&&window.microphoneStopped()",completionHandler:nil) }
    func endInput(_ reason:String="pause_boundary") {
        inputEndReason=reason;trace("end_input",reason);trace("end_input_timing","reason=\(reason),engine_seconds=\(Date().timeIntervalSince1970-captureStarted)")
        guard active, !awaitingFinal else { return }
        awaitingFinal=true
        if diagnosticOnly {_ = diagnosticSession.ending();publishDiagnostic()}
        timer?.invalidate(); timer=nil
        sessionTimer?.invalidate(); sessionTimer=nil
        // Stop appending first, but keep the engine/tap alive until ASR drains.
        // Stopping the engine before endAudio previously coincided with empty finals.
        microphoneStats.closeInput(request)
        let token=generation
        finalWaitTimer=Timer.scheduledTimer(withTimeInterval:3,repeats:false) { _ in
            if self.active && self.generation==token { self.finish(recognitionFinal:false,reason:self.inputEndReason+"_final_timeout") }
        }
        event("status","本句音频已提交，正在等待最终转写；音量计仍在本机显示。")
    }
    func finish(recognitionFinal: Bool = true, reason: String = "asr_final") {
        guard active else { return }
        let text = pending.trimmingCharacters(in:.whitespacesAndNewlines)
        let once=singleUtterance
        if diagnosticOnly {
            let retained=text.isEmpty ? diagnosticSession.partial : text
            if !retained.isEmpty {event("diagnostic_result",retained,recognitionFinal:recognitionFinal && !text.isEmpty,reason:reason)}
            stop("diagnostic_complete");event("status","麦克风诊断已结束；未执行查询或修改。请查看音量、音频帧与识别诊断。",reason:"diagnostic_complete");return
        }
        if text.isEmpty && !once && testAudioFiles.isEmpty {
            reset();let next=generation
            event("status",reason=="asr_empty_final" ? "识别器未确认最终文字，已保留上方未确认转写供手动核对；继续等待说话。" : "正在等待说话；安静不算故障，当前合同和候选保持。",reason:reason)
            DispatchQueue.main.asyncAfter(deadline:.now()+0.7) {if self.active && self.generation==next {self.listen()}};return
        }
        if text.isEmpty && !once {recover(reason+"_empty","本轮没有识别到文字");return}
        if !recognitionFinal && !once {if !text.isEmpty {event("final",text,recognitionFinal:false,reason:reason)};recover(reason,"尚未取得最终转写，本句没有应用");return}
        reset()
        if once { active=false }
        let restartGeneration = generation
        if recognitionFinal && !text.isEmpty {recoveryCount=0}
        if !text.isEmpty { event(diagnosticOnly ? "diagnostic_result" : "final",text,recognitionFinal:recognitionFinal,reason:reason) }
        event("status",once ? (!recognitionFinal && !text.isEmpty ? "未取得最终转写（\(reason)），本句未应用，麦克风已关闭；可校正文字或恢复连续监听。" : text.isEmpty ? "单句录音结束但未识别到文字（\(reason)）。合同与候选保持；可点击恢复连续监听。" : "单句转写完成，麦克风已关闭；可点击恢复连续监听。") : (text.isEmpty ? "本轮没有识别到文字（\(reason)），保留合同并准备继续监听…" : "本句已结束，正在准备下一句监听…"),reason:reason)
        DispatchQueue.main.asyncAfter(deadline:.now()+0.25) { if self.active && self.generation == restartGeneration { self.listen() } }
    }
    func listen() {
        guard active, let rec = recognizer else { return }
        guard rec.isAvailable, rec.supportsOnDeviceRecognition else { stop("recognizer_unavailable"); event("error","本机识别当前不可用，监听已停止。稍后点击开启连续语音重试。"); return }
        reset()
        let token = generation
        if !testAudioFiles.isEmpty {listenTestFile(rec,token);return}
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.contextualStrings = contextualTerms
        req.requiresOnDeviceRecognition = true
        req.shouldReportPartialResults = true
        request = req
        req.taskHint = .dictation
        let input = engine.inputNode
        let format = input.outputFormat(forBus:0)
        guard format.sampleRate > 0, format.channelCount > 0 else { stop("input_device_unavailable"); event("error","未找到可用麦克风输入设备。"); return }
        microphoneStats.reset();captureStarted=Date().timeIntervalSince1970;firstResultLogged=false;noFramesReported=false;lastMeterLog=0
        captureInfo=["device":inputDeviceName(),"sample_rate":format.sampleRate,"channels":format.channelCount,"format":"\(format.commonFormat.rawValue)","speech_permission":SFSpeechRecognizer.authorizationStatus().rawValue,"microphone_permission":AVCaptureDevice.authorizationStatus(for:.audio).rawValue,"request_id":UUID().uuidString,"generation":token,"diagnostic_only":diagnosticOnly]
        trace("capture_setup",String(data:try! JSONSerialization.data(withJSONObject:captureInfo,options:.sortedKeys),encoding:.utf8) ?? "")
        input.installTap(onBus:0,bufferSize:1024,format:format) { buffer,_ in _ = self.microphoneStats.consume(buffer,req) }
        hasTap = true
        task = rec.recognitionTask(with:req) { result,error in self.recognition(result,error,token) }
        do {
            engine.prepare(); try engine.start()
            captureStarted=Date().timeIntervalSince1970
            if diagnosticOnly {_ = diagnosticSession.engineStarted(captureStarted);publishDiagnostic()}
            trace("engine_started","request=\(captureInfo["request_id"] ?? ""),countdown_started=true")
            meterTimer=Timer.scheduledTimer(withTimeInterval:0.25,repeats:true) { _ in self.meter(token) }
            sessionTimer = Timer.scheduledTimer(withTimeInterval:singleUtterance ? 10 : 45,repeats:false) { _ in
                if self.active && self.generation == token { self.endInput(self.singleUtterance ? "single_deadline_10s" : "continuous_request_rotation_45s") }
            }
            event("status",singleUtterance ? "单句录音中，最多10秒；结束后自动关闭麦克风。" : "连续监听中；可能听到旁人。语音只预览，保存须按键。")
        }
        catch { recover("engine_start_error","麦克风启动失败：\(error.localizedDescription)") }
    }
    func recognition(_ result:SFSpeechRecognitionResult?,_ error:Error?,_ token:Int) {
        DispatchQueue.main.async {
            guard self.active,token==self.generation else{self.trace("stale_recognition_ignored","callback=\(token),current=\(self.generation)");return}
            if let result=result {
                let text=result.bestTranscription.formattedString
                if !self.firstResultLogged {self.firstResultLogged=true;self.trace("first_asr_result","ms=\(Int((Date().timeIntervalSince1970-self.captureStarted)*1000)),final=\(result.isFinal),chars=\(text.count)")}
                self.trace("asr_result","final=\(result.isFinal),chars=\(text.count)")
                if self.diagnosticOnly {self.diagnosticSession.record(text)}
                if !text.isEmpty && text != self.pending {self.pending=text;self.lastNonemptyPartial=text;self.event("partial",text)}
                if result.isFinal {
                    if text.isEmpty {
                        self.trace("empty_final","last_partial_chars=\(self.lastNonemptyPartial.count)")
                        if !self.lastNonemptyPartial.isEmpty {self.event("final",self.lastNonemptyPartial,recognitionFinal:false,reason:"empty_final_after_partial")}
                        self.pending="";self.finish(recognitionFinal:false,reason:"asr_empty_final");return
                    }
                    self.finish();return
                }
            }
            if let error=error {
                self.trace("asr_error","domain=\((error as NSError).domain),code=\((error as NSError).code),awaiting_final=\(self.awaitingFinal)")
                if self.diagnosticOnly {if !self.diagnosticSession.partial.isEmpty {self.event("diagnostic_result",self.diagnosticSession.partial,recognitionFinal:false,reason:"diagnostic_asr_error_partial")};self.stop("diagnostic_asr_error");self.event("error","麦克风诊断：识别器返回 \((error as NSError).domain) / \((error as NSError).code)；\(error.localizedDescription)。未查询或保存；请核对音频帧与音量。",reason:"diagnostic_asr_error");return}
                if self.awaitingFinal && !self.pending.isEmpty {self.finish(recognitionFinal:false,reason:"asr_error_partial");return}
                let noSpeech=(error as NSError).code==1110 || error.localizedDescription.lowercased().contains("no speech detected")
                self.trace("asr_error","domain=\((error as NSError).domain),code=\((error as NSError).code),awaiting_final=\(self.awaitingFinal)")
                if noSpeech && !self.singleUtterance && self.testAudioFiles.isEmpty {
                    self.pending="";self.finish(recognitionFinal:false,reason:"normal_silence");return
                }
                self.recover(noSpeech ? "no_speech" : "recognizer_error_\((error as NSError).code)",noSpeech ? "未识别到清晰语音" : "本机识别中断：\(error.localizedDescription)")
            }
        }
    }
    func listenTestFile(_ rec:SFSpeechRecognizer,_ token:Int) {
        web.evaluateJavaScript("typeof boundPath==='string' && boundPath.includes('/var/audio-tests/')") { value,_ in
            guard self.active,token==self.generation else{return}
            guard value as? Bool == true else {self.stop();self.event("error","合成音频测试仅允许独立音频测试副本绑定，不使用麦克风。");return}
            guard self.testAudioIndex<self.testAudioFiles.count else {self.stop();self.event("status","合成音频样本已完成；没有使用麦克风。");return}
            let url=self.testAudioFiles[self.testAudioIndex];self.testAudioIndex+=1
            let req=SFSpeechURLRecognitionRequest(url:url);req.contextualStrings=self.contextualTerms;req.requiresOnDeviceRecognition=true;req.shouldReportPartialResults=true;req.taskHint = .dictation
            self.fileRequest=req
            self.task=rec.recognitionTask(with:req) {result,error in self.recognition(result,error,token)}
            self.sessionTimer=Timer.scheduledTimer(withTimeInterval:25,repeats:false) { _ in if self.active && token==self.generation {self.finish(recognitionFinal:false,reason:"audio_file_timeout")} }
            self.event("status","本机合成PCM音频验收，第\(self.testAudioIndex)段；不使用麦克风、不联网。")
        }
    }

}
let app = NSApplication.shared
let delegate = ArchiveApp()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
