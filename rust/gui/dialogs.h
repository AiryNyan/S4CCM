// gui/dialogs.py
#pragma once

#include "common.h"

class SetupDialog : public QDialog {
public:
    QString resultDir;
    explicit SetupDialog(QWidget* parent = nullptr, const QString& current = {});

protected:
    void dragEnterEvent(QDragEnterEvent* e) override;
    void dropEvent(QDropEvent* e) override;

private:
    void browse();
    QString chosen() const;
    void validate();
    void acceptChoice();

    QButtonGroup* group;
    QRadioButton* manualRb;
    QLineEdit* edit;
    QLabel* status;
    QDialogButtonBox* buttons;
};

// plan 은 코어의 import.analyze 결과. 확인을 누르면 edits() 에 고친 내용이 담긴다.
class ImportDialog : public QDialog {
public:
    ImportDialog(const QJsonObject& plan, QWidget* parent = nullptr);
    QJsonArray edits() const { return edits_; }

private:
    struct Child {
        QTreeWidgetItem* item;
        int entryIndex;
        CategoryCombo* combo;
    };
    struct Row {
        QComboBox* mode;
        QLineEdit* nameEdit;
        QList<Child> children;
        QCheckBox* replaceChk;
        QList<QTreeWidgetItem*> trayRows;
        int entryCount;
    };
    static void modeChanged(QComboBox* mode, const QList<Child>& children, QLineEdit* nameEdit);
    void acceptPlan();

    QTreeWidget* tree;
    QList<Row> rows;
    QJsonArray edits_;
};
