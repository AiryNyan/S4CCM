// gui/pages.py
#pragma once

#include "common.h"

class OrganizePage : public Page {
public:
    std::function<void()> changed;
    OrganizePage();
    void refreshPage() override;

private:
    struct Plan {
        ItemPtr item;
        QString targetRel;
    };
    void recat(int row, CategoryCombo* combo);
    void checkAll(bool on);
    void apply();

    QList<Plan> plans;
    QTableWidget* table;
    QList<QCheckBox*> checks;
    QLabel* summary;
    QPushButton* bApply;
};

class ResourceCfgPage : public Page {
public:
    ResourceCfgPage();
    void refreshPage() override;

private:
    QStringList folders() const;
    void addRow(int priority, const QString& folder, int depth);
    void delRow(QPushButton* button);
    QJsonObject collect() const;
    void updatePreview();
    void loadCfg(const QJsonObject& cfg);
    void checkOverridesRule();
    void defaults();
    void save();
    void saveOptions();

    QJsonObject cfg_;   // {"rules": [[우선순위, 폴더, 깊이]], "extra": [...]}
    QTableWidget* table;
    QCheckBox *chkCc, *chkScript;
    QLabel *optStatus, *status;
    QPlainTextEdit* preview;
};

class BisectPage : public Page {
public:
    std::function<void()> changed;
    static constexpr int WATCH_START_TIMEOUT = 180;

    BisectPage();
    void refreshPage() override;
    bool confirmLeave(bool closing = false);

private:
    QList<ItemPtr> scopeItems();
    bool gameClosedOrWarn(const QString& what);
    void start();
    void launch();
    void watchGame();
    void stopWatch();
    void askResult(const QString& note);
    void report(bool worked);
    int run(const QString& title, const QString& phase, const char* method, const QJsonObject& args, bool raiseErrors = false);
    void end();
    void fill(QListWidget* lw, const QJsonArray& ids);
    QJsonObject state() const;

    QString watchState;   // 비어 있으면 None
    double watchStarted = 0;
    QTimer* watchTimer;
    QGroupBox *setupBox, *runBox;
    QButtonGroup* scope;
    QRadioButton *rbAll, *rbCat, *rbRecent;
    QComboBox* catCombo;
    QSpinBox* days;
    QCheckBox* chkScripts;
    QPushButton *bStart, *bLaunch, *bOk, *bBad, *bEnd;
    QLabel *progressLabel, *hint;
    QProgressBar* progress;
    QTabWidget* tabs;
    QListWidget *listIn, *listOut, *listCleared;
};

class DuplicatesPage : public Page {
public:
    std::function<void()> changed;
    Async task;

    DuplicatesPage();
    void run();

private:
    void setButtons(bool on);
    void done();
    void show(const QJsonArray& groups);
    QList<ItemPtr> checked() const;
    void apply(const QString& action);

    QJsonValue result_;
    QString error_;
    bool failed_ = false;
    QPushButton *bRun, *bDisable, *bTrash;
    QProgressBar* progress;
    QLabel* summary;
    QTreeWidget* tree;
};

class SettingsPage : public Page {
public:
    std::function<void()> simsDirChanged;
    std::function<void(const QString&)> themeChanged;
    std::function<void()> changed;

    SettingsPage();
    void refreshPage() override;

private:
    void undoStatus();
    void undoOrganize();
    void exeStatus();
    void changeSims();
    void browseExe();
    void autoExe();
    void save();
    void themePicked();

    QLineEdit *sims, *exe;
    QLabel *exeStatusLabel, *undoStatusLabel;
    QCheckBox* chkCache;
    QComboBox* theme;
    QPushButton* bUndo;
};
