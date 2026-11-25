// gui/main_window.py
#pragma once

#include "common.h"

class LibraryPage;
class OrganizePage;
class ResourceCfgPage;
class BisectPage;
class TrayPage;
class DuplicatesPage;
class SettingsPage;
class SymlinkPage;

class MainWindow : public QMainWindow {
public:
    MainWindow();
    void selfTest(const QString& dir);   // 검증용: 각 화면을 이미지로 저장하고 끝낸다 (S4CCM_SELFTEST)
    void importSelfTest(const QString& dir, const QString& path);   // 검증용: 가져오기 과정을 캡처 (S4CCM_SELFTEST_IMPORT)
    // 관리자 권한으로 다시 켜진 S4CCM 에서: 시작 스캔이 끝나면 Mods 를 옮기고 종료 코드와 함께 끝낸다.
    void resumeRelocate(const QString& dest);

protected:
    void changeEvent(QEvent* e) override;
    void dragEnterEvent(QDragEnterEvent* e) override;
    void dropEvent(QDropEvent* e) override;
    void closeEvent(QCloseEvent* e) override;

private:
    // 사이드바 항목에 붙는 값. 빈 문자열은 Python 의 None.
    struct Side {
        QString type, group, category, status, page;
        QTreeWidgetItem* item = nullptr;
    };
    QAction* act(QToolBar* tb, const QString& text, std::function<void()> fn, const QKeySequence& shortcut = {}, const QString& tip = {});
    void startup();
    void simsDirChanged();
    void buildSidebar();
    void paintSidebar();
    void themeChanged(const QString& theme);
    void updateCounts();
    void sidebarClicked(QTreeWidgetItem* wi);
    bool goTo(const QString& page);
    void rescan(bool show = false);
    void scanDone();
    void refreshCurrentPage();
    void onSearch(const QString& text);
    void toggleView();
    void detailAction(const QString& action);
    void setStatus(const QList<ItemPtr>& items, const QString& to);
    void addFiles();
    void addFolder();
    void importPaths(QStringList paths);
    void checkScriptOptions();
    void elevateRelocate(const QString& dest);
    // 자동 업데이트 (GitHub 릴리스)
    void checkForUpdate();
    void offerUpdate(const QJsonObject& release);
    void runUpdate(const QJsonObject& release);

    Async scan_;
    Async updateCheck_;
    QString scanError_;
    bool scanFailed_ = false;
    bool rescanPending_ = false, closing_ = false;
    QElapsedTimer lastScan_;

    QLineEdit* search;
    QAction* viewAct;
    LibraryPage* library;
    OrganizePage* organize;
    ResourceCfgPage* cfgpage;
    BisectPage* bisect;
    TrayPage* tray;
    DuplicatesPage* dupes;
    SymlinkPage* symlink;
    SettingsPage* settings;
    QStackedWidget* pages;
    QHash<QString, Page*> pageMap;
    QTreeWidget* sidebar;
    QList<Side> sideItems;
    QLabel *statusLabel_, *pathLabel;
    QProgressBar* scanBar;
};
