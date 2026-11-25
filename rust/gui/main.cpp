// gui/app.py
#include "common.h"
#include "main_window.h"

#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
#include <shobjidl.h>


static QFile* g_log = nullptr;
static QString g_crashLog;

static void logLine(const QString& text) {
    if (g_log && g_log->isOpen()) {
        g_log->write(text.toUtf8());
        g_log->flush();
    }
}

static void openLog() {
    g_crashLog = coreCall("app_dir").toString() + u"\\crash.log"_s;
    if (QFileInfo::exists(g_crashLog) && QFileInfo(g_crashLog).size() > 1000000) {
        QFile::remove(g_crashLog + u".old"_s);
        QFile::rename(g_crashLog, g_crashLog + u".old"_s);
    }
    g_log = new QFile(g_crashLog);
    g_log->open(QIODevice::Append);
    logLine(u"\n=== %1 start ===\n"_s.arg(QDateTime::currentDateTime().toString(u"yyyy-MM-dd HH:mm:ss"_s)));
}

static void qtMessages(QtMsgType type, const QMessageLogContext&, const QString& msg) {
    const char* name = type == QtWarningMsg ? "QtWarningMsg" : type == QtCriticalMsg ? "QtCriticalMsg" : type == QtFatalMsg ? "QtFatalMsg" : nullptr;
    if (name)
        logLine(u"[Qt %1] %2\n"_s.arg(QLatin1String(name), msg));
}

static void unexpected(const QString& what) {
    logLine(what + u'\n');
    QMessageBox::warning(nullptr, u"S4CCM 오류"_s,
                         u"예상하지 못한 오류가 생겼습니다.\n\n%1\n\n자세한 내용은 다음 파일에 저장했습니다:\n%2"_s.arg(what, g_crashLog));
}

// 화면 코드가 잡지 않은 코어 오류를 알린다 (Python 판의 sys.excepthook).
class App : public QApplication {
public:
    using QApplication::QApplication;
    bool notify(QObject* receiver, QEvent* event) override {
        try {
            return QApplication::notify(receiver, event);
        } catch (const CoreError& ex) {
            while (QApplication::overrideCursor())
                QApplication::restoreOverrideCursor();
            unexpected(ex.msg);
        } catch (const std::exception& ex) {
            unexpected(QString::fromUtf8(ex.what()));
        }
        return false;
    }
};

int main(int argc, char** argv) {
    // 관리자 권한으로 다시 켜질 때 받는 인자: --relocate <옮길 곳> [--home <설정 폴더>]
    QString relocateDest;
    {
        int n = 0;
        wchar_t** w = CommandLineToArgvW(GetCommandLineW(), &n);
        for (int i = 1; w && i + 1 < n; ++i) {
            const QString key = QString::fromWCharArray(w[i]);
            const QString val = QString::fromWCharArray(w[i + 1]);
            if (key == u"--home"_s)
                qputenv("S4CCM_HOME", val.toUtf8());
            else if (key == u"--relocate"_s)
                relocateDest = val.endsWith(u"\\."_s) ? val.chopped(1) : val;
        }
        LocalFree(w);
    }
    SetCurrentProcessExplicitAppUserModelID(L"S4CCM");
    try {
        openLog();
    } catch (const CoreError&) {
    }
    qInstallMessageHandler(qtMessages);
    App app(argc, argv);
    app.setApplicationName(u"S4CCM"_s);
    const QString icon = QCoreApplication::applicationDirPath() + u"/assets/icon.png"_s;
    if (QFileInfo::exists(icon))
        app.setWindowIcon(QIcon(icon));
    try {
        coreInit();
        // 지난 업데이트에서 남은 옛 파일과 받아 둔 업데이트 파일을 정리한다.
        coreCall("update.cleanup", {{"app_dir"_L1, QDir::toNativeSeparators(QCoreApplication::applicationDirPath())}});
    } catch (const CoreError& ex) {
        unexpected(ex.msg);
        return 1;
    }
    applyTheme(cfg().theme);
    int code = 1;
    {
        MainWindow win;
        win.show();
        const QString selfTest = qEnvironmentVariable("S4CCM_SELFTEST");
        const QString importTest = qEnvironmentVariable("S4CCM_SELFTEST_IMPORT");
        if (!relocateDest.isEmpty())
            QTimer::singleShot(500, &win, [&win, relocateDest] { win.resumeRelocate(relocateDest); });
        else if (!selfTest.isEmpty() && !importTest.isEmpty())
            QTimer::singleShot(1500, &win, [&win, selfTest, importTest] { win.importSelfTest(selfTest, importTest); });
        else if (!selfTest.isEmpty())
            QTimer::singleShot(1500, &win, [&win, selfTest] { win.selfTest(selfTest); });
        code = app.exec();
    }
    logLine(u"=== exit %1 ===\n"_s.arg(code));
    return code;
}
