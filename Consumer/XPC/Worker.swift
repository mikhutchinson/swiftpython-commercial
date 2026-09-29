import SwiftPythonWorkerService

@main enum WorkerMain {
    static func main() throws {
        try PythonWorkerXPCService.run()
    }
}
