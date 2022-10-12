# import packages
import os
import glob
import shutil

import pandas as pd
import numpy as np
import torch
import tqdm as t
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import seaborn as sns
import tqdm
from sklearn.metrics import accuracy_score
from numpy import unravel_index
import math
from shutil import copyfile

# import scripts
# from detectors.yolov5.utils.metrics import bbox_iou
from scripts.utils_confusionmatrix import cm_analysis


def bbox_iou(box1, box2, xywh=True, GIoU=False, DIoU=False, CIoU=False, eps=1e-7):
    # Returns Intersection over Union (IoU) of box1(1,4) to box2(n,4)

    # Get the coordinates of bounding boxes
    if xywh:  # transform from xywh to xyxy
        (x1, y1, w1, h1), (x2, y2, w2, h2) = box1.chunk(4, 1), box2.chunk(4, 1)
        w1_, h1_, w2_, h2_ = w1 / 2, h1 / 2, w2 / 2, h2 / 2
        b1_x1, b1_x2, b1_y1, b1_y2 = x1 - w1_, x1 + w1_, y1 - h1_, y1 + h1_
        b2_x1, b2_x2, b2_y1, b2_y2 = x2 - w2_, x2 + w2_, y2 - h2_, y2 + h2_
    else:  # x1, y1, x2, y2 = box1
        b1_x1, b1_y1, b1_x2, b1_y2 = box1.chunk(4, 1)
        b2_x1, b2_y1, b2_x2, b2_y2 = box2.chunk(4, 1)
        w1, h1 = b1_x2 - b1_x1, b1_y2 - b1_y1
        w2, h2 = b2_x2 - b2_x1, b2_y2 - b2_y1

    # Intersection area
    inter = (torch.min(b1_x2, b2_x2) - torch.max(b1_x1, b2_x1)).clamp(0) * \
            (torch.min(b1_y2, b2_y2) - torch.max(b1_y1, b2_y1)).clamp(0)

    # Union Area
    union = w1 * h1 + w2 * h2 - inter + eps

    # IoU
    iou = inter / union
    if CIoU or DIoU or GIoU:
        cw = torch.max(b1_x2, b2_x2) - torch.min(b1_x1, b2_x1)  # convex (smallest enclosing box) width
        ch = torch.max(b1_y2, b2_y2) - torch.min(b1_y1, b2_y1)  # convex height
        if CIoU or DIoU:  # Distance or Complete IoU https://arxiv.org/abs/1911.08287v1
            c2 = cw ** 2 + ch ** 2 + eps  # convex diagonal squared
            rho2 = ((b2_x1 + b2_x2 - b1_x1 - b1_x2) ** 2 + (b2_y1 + b2_y2 - b1_y1 - b1_y2) ** 2) / 4  # center dist ** 2
            if CIoU:  # https://github.com/Zzh-tju/DIoU-SSD-pytorch/blob/master/utils/box/box_utils.py#L47
                v = (4 / math.pi ** 2) * torch.pow(torch.atan(w2 / (h2 + eps)) - torch.atan(w1 / (h1 + eps)), 2)
                with torch.no_grad():
                    alpha = v / (v - iou + (1 + eps))
                return iou - (rho2 / c2 + v * alpha)  # CIoU
            return iou - rho2 / c2  # DIoU
        c_area = cw * ch + eps  # convex area
        return iou - (c_area - union) / c_area  # GIoU https://arxiv.org/pdf/1902.09630.pdf
    return iou  # IoU

def percentile(n):
    def percentile_(x):
        return np.percentile(x, n)
    percentile_.__name__ = 'percentile_%s' % n
    return percentile_

def calculate_ious(series):
    # claculate iou, class doenst factor in!
    # input: pair label(s) and prediction(s) from one image/label file
    ious = []
    records = []
    # 1st go through each label
    for i in range(len(series['bbox_label'])):
        # 2nd go through each prediction
        for j in range(len(series['bbox_pred'])):
            # convert label bbox from numpy to pytoch tensor
            bbox_label = np.array(series['bbox_label'][i]).squeeze()
            bbox_label_torch = torch.from_numpy(bbox_label)
            bbox_label_torch = bbox_label_torch[None, :]

            # convert prediction bbox from numpy to pytoch tensor
            bbox_prediction = np.array(series['bbox_pred'][j]).squeeze()
            bbox_prediction_torch = torch.from_numpy(bbox_prediction)
            bbox_prediction_torch = bbox_prediction_torch[None, :]

            # if there is a dummy predcition or label present, set iou to -1
            if series['class_label'][i] < 0 or series['class_pred'][j] < 0:
                iou = -1.0
            # calculate actual iou
            else:
                iou_torch = bbox_iou(bbox_label_torch, bbox_prediction_torch)
                iou = iou_torch.numpy().squeeze()
            # append iou to list
            ious.append(iou)
            # create a record with all necessary informations
            record = {
                'ID': series.ID,
                'file_pred': series.file_pred,
                'class_pred': series.class_pred[j],
                'bbox_pred': series.bbox_pred[j],
                'file_label': series.file_label,
                'class_label': series.class_label[i],
                'bbox_label': series.bbox_label[i],
                'iou': iou,
            }
            records.append(record)

    # create dataframe from all possible iou combinations
    df = pd.DataFrame(records)

    # in case of multiple labels and predcitions all iou combinations have been calculated
    # only select highest lable prediction pair with the highest iou
    # 1st sort indicies based on iou value
    indicies_iou_sort_arr = np.argsort(ious)
    # 2nd create a list of sorted indicies
    indicies_iou_sort_list = list(indicies_iou_sort_arr)
    # 3rd reverse sorted list so the indicies of the highest ious are on top
    indicies_iou_sort_list = list(reversed(indicies_iou_sort_list))
    # 4th get the first n results from label prediction pair
    # n = sqrt(length of all possible label prediction pairs)
    # e.g 2 labels 2 prediction = 4 iou pairs --> n = sqrt(4) = 2 ious
    indicies_iou = indicies_iou_sort_list[0:int(np.sqrt(len(df)))]
    # get the actual iou pair by their indicies
    df_iou_pairs = df.iloc[indicies_iou, :]

    return df_iou_pairs

def get_file_info(path_list):
    # function to get necessary information of label or prediction
    # get file ID name, path to file, class, bounding box
    # function returns a list of dictionaries
    records = []
    for file in path_list:
        class_ids = []
        bboxes = []
        file_ids = []
        file_id = file.split(os.sep)[-1]
        file_id = file_id.split('.txt')[0]
        with open(file) as lf:
            label_lines_str = lf.readlines()
            for i, info in enumerate(label_lines_str):
                file_ids.append(file_id)
                label_info_str = label_lines_str[i].split(" ")[:5]
                label_floats = [float(f) for f in label_info_str]
                class_id = label_floats[0]
                class_ids.append(class_id)
                bbox = label_floats[1:]
                bboxes.append(bbox)
            record = {
                'ID': file_ids[0],
                'file': file,
                'class': class_ids,
                'bbox': bboxes,
            }
            records.append(record)
    return records

def run_evaluate(data_path='1', plot_cm=False):
    """

    :param input:
    :return:
    """

    # get all prediction files in data_path
    predictions_list = glob.glob(data_path + os.sep + '*')
    # if there are any predictions in the threshold folder or not
    if len(predictions_list) > 0:
        # run get_file_info function if there are predictions and read prediction files to create a dataframe
        records = get_file_info(predictions_list)
        df_predictions = pd.DataFrame(records)
    else:
        # if there are no predictions return an empty dataframe
        df_predictions = pd.DataFrame(columns=['ID', 'file', 'class', 'bbox'])

    # get all label files from a fixed path and create label dataframe
    labels_list = glob.glob(r'F:\202105_PAI\data\P1_Data_sampled\test_valentin\labels' + os.sep + '*')
    records = get_file_info(labels_list)
    df_labels = pd.DataFrame(records)

    # merge label and prediction dataframe
    # merge on ID (file name) if there is no matching label/prediction a nan row will be added
    df = pd.merge(df_predictions, df_labels, on='ID', how="outer", suffixes=('_pred', '_label'))

    # replace nan in dataframe for labels or predictions with dummy values so ious can be calculated
    for index, row in tqdm.tqdm(df.iterrows(), total=df.shape[0]):
        if not isinstance(row['class_pred'], list):
            df.at[index, 'class_pred'] = [-1.0]
            df.at[index, 'bbox_pred'] = [[.0, .0, .0, .0]]
        if not isinstance(row['class_label'], list):
            df.at[index, 'class_label'] = [-1.0]
            df.at[index, 'bbox_label'] = [[.0, .0, .0, .0]]

    # loop through each label and prediction pair
    # if there is a missmatch of numbers ad a dummy value so iou can be calculated
    for index, row in tqdm.tqdm(df.iterrows(), total=df.shape[0]):
        # check if there are less labels than predictions
        if len(row['class_label']) < len(row['class_pred']):
            # calculate the difference between number of labels and predictions
            diff_len = np.abs(len(row['class_label']) - len(row['class_pred']))
            for i in range(diff_len):
                class_label = row['class_label']
                class_label.append(-1.0)
                df.at[index, 'class_label'] = class_label

                class_bbox = row['bbox_label']
                class_bbox.append([.0, .0, .0, .0])
                df.at[index, 'bbox_label'] = class_bbox

        # check if there are more labels than predictions
        elif len(row['class_label']) > len(row['class_pred']):
            diff_len = np.abs(len(row['class_label']) - len(row['class_pred']))
            for i in range(diff_len):
                class_pred = row['class_pred']
                class_pred.append(-1.0)
                df.at[index, 'class_pred'] = class_pred

                class_bbox = row['bbox_pred']
                class_bbox.append([.0, .0, .0, .0])
                df.at[index, 'bbox_pred'] = class_bbox

    # calclulate iou pairs
    records = []
    # loop through each prediction and label pair to calculate iou
    for index, row in tqdm.tqdm(df.iterrows(), total=df.shape[0]):
        df_iou_combination = calculate_ious(row)
        # for each returned iou(s) add single iou to a list
        for _, record in df_iou_combination.iterrows():
            records.append(record.to_dict())

    # for each single iou
    df_ious = pd.DataFrame(records)
    df_ious_groupPercentile = df_ious.groupby('class_label')['iou'].agg([percentile(1), percentile(25), percentile(50),
                                                                         percentile(75), percentile(99)])
    # print(df_ious_groupPercentile)


    y_true_list = list(df_ious['class_label'])
    y_true_list = [int(f) + 1 if f >= 0 else 0 for f in y_true_list]
    y_pred_list = list(df_ious['class_pred'])
    y_pred_list = [int(f) + 1 if f >= 0 else 0 for f in y_pred_list]

    labels = ['Background', 'Araneae', 'Coleoptera', 'Diptera', 'Hemiptera',
              'Hymenoptera_Formicidae', 'Hymenoptera', 'Lepidoptera', 'Orthoptera']

    results_path = data_path.split(os.sep)
    results_path = os.path.join(*results_path[:-1])
    filename = os.path.join(results_path, 'confusion_matrix.png')
    filename_tex = os.path.join(results_path, 'confusion_matrix_tex.txt')
    df_ious_groupPercentile.to_csv(os.path.join(results_path, 'class_ious_percentiles.csv'))
    if plot_cm:
        cm = cm_analysis(y_true_list, y_pred_list, labels, ymap=None, figsize=(9, 9),
                         filename=filename, filename_tex=filename_tex, plot='plot')
    else:
        cm = cm_analysis(y_true_list, y_pred_list, labels, ymap=None, figsize=(9, 9),
                         filename=filename, filename_tex=filename_tex, plot=None)

    FP = cm.sum(axis=0) - np.diag(cm)
    FN = cm.sum(axis=1) - np.diag(cm)
    TP = np.diag(cm)
    TN = cm.sum() - (FP + FN + TP)

    FP = FP.astype(float)
    FN = FN.astype(float)
    TP = TP.astype(float)
    TN = TN.astype(float)

    # False positive rate
    FPR = np.divide(FP, (FP + TN + 1e-5))

    # Precision
    P = np.divide(TP, (TP + FP + 1e-5))

    # Recall
    R = np.divide(TP, (TP + FN + 1e-5))

    print('Accuracy: {:.4f}'.format(accuracy_score(y_true_list, y_pred_list)))
    print('FPR: {:.4f}'.format(np.mean(FPR)))
    print('Precision: {:.4f}'.format(np.mean(P)))
    print('Recall: {:.4f}'.format(np.mean(R)))

    matching_ids = []
    for i in range(len(y_true_list)):
        if y_true_list[i] == y_pred_list[i]:
            matching_ids.append(i)

    df_ious_matching_classes = df_ious.iloc[matching_ids]
    iou_match = df_ious_matching_classes['iou'].mean()
    print('IoU: {:.4f}'.format(iou_match))

    filename_metrics = filename_tex.replace('confusion_matrix_tex', 'metrics')
    with open(filename_metrics, 'w') as f:
        f.write('OA: {:6.4f}, FPR: {:6.4f}, Precision: {:6.4f}, Recall: {:6.4f}, IoU: {:6.4f}, matchIoU: {:6.4f}'.
                format(
            accuracy_score(y_true_list, y_pred_list),
            np.mean(FPR),
            np.mean(P),
            np.mean(R),
            df_ious['iou'].mean(),
            df_ious_matching_classes['iou'].mean()
        ))

    return accuracy_score(y_true_list, y_pred_list), iou_match, np.mean(FPR)

if __name__ == '__main__':
    # select path to results
    # list all file in path directory
    source_path = r'F:\202105_PAI\data\P1_results\yolov7_img640_b8_e300_hyp_custom'
    all_results = glob.glob(source_path + '\*')
    # select yolo-version for naming and searching for labels since process for v4 is different than v5 and v7
    yoloversion = 'yolov7'
    # only select path if it is a folder
    yolo_results = [f for f in all_results if os.path.isdir(f)]
    # get parent directory if only one threshold combination is selected
    if len(yolo_results) == 1:
        yolo_results = [(os.path.dirname(yolo_results[0]))]
    # create empty lists for accuarcy metrics
    mean_iou_list = []
    mean_oa_list = []
    FPR_list = []

    # go through each folder and calculate accuracy metrics and append it to lists
    for data_i, data_path in enumerate(yolo_results):
        print('{} of {} datasets'.format(data_i, len(yolo_results)))
        if not yoloversion == 'yolov4':
            data_path = os.path.join(data_path, 'labels')
        mean_oa, mean_iou, FPR = run_evaluate(data_path=data_path, plot_cm=False)
        mean_iou_list.append(mean_iou)
        mean_oa_list.append(mean_oa)
        FPR_list.append(FPR)

    # Turn list of all 81 (9*9) results into 9*9 array
    iou_array = np.reshape(np.array(mean_iou_list), (-1, 9))
    oa_array = np.reshape(np.array(mean_oa_list), (-1, 9))
    FPR_array = np.reshape(np.array(FPR_list), (-1, 9))

    # hardcode labels for thresholds
    metric_threshold = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    conf_threshold = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

    max_oa = unravel_index(oa_array.argmax(), oa_array.shape)

    print('Highest OA at confidence {:.1f} and iou {:.1f}'.format(conf_threshold[max_oa[0]], metric_threshold[max_oa[1]]))
    src_file = source_path + r'\results_at_conf_' + \
            str(conf_threshold[max_oa[0]]) + '_iou_' + \
            str(conf_threshold[max_oa[1]]) + '\metrics.txt'

    dst_file = source_path + r'\best_metricsat_conf_' + \
            str(conf_threshold[max_oa[0]]) + '_iou_' + \
            str(conf_threshold[max_oa[1]]) + '.txt'
    copyfile(src_file, dst_file)

    # create heatmaps for all accuracy metrics
    cm = pd.DataFrame(iou_array, index=conf_threshold, columns=metric_threshold)
    fig, ax = plt.subplots(figsize=(8,8))
    sns.heatmap(cm, annot=True, fmt='.4f', ax=ax, cmap='Blues', square=True, vmin=0, vmax=1)
    ax.set_xlabel('\nIoU Threshold')
    ax.set_ylabel('Confidence Threshold\n')
    ax.figure.tight_layout()
    ax.figure.subplots_adjust(bottom=0.2)
    ax.invert_yaxis()
    plt.title('IoU')
    plt.savefig(r'F:\202105_PAI\data\P1_results\thresholds_' + yoloversion + '_iou.png')
    plt.show()

    cm = pd.DataFrame(oa_array, index=conf_threshold, columns=metric_threshold)
    fig, ax = plt.subplots(figsize=(8,8))
    sns.heatmap(cm, annot=True, fmt='.4f', ax=ax, cmap='Blues', square=True, vmin=0, vmax=1)
    ax.set_xlabel('\nIoU Threshold')
    ax.set_ylabel('Confidence Threshold\n')
    ax.figure.tight_layout()
    ax.figure.subplots_adjust(bottom=0.2)
    ax.invert_yaxis()
    plt.title('Overall Accuracy')
    plt.savefig(r'F:\202105_PAI\data\P1_results\thresholds_' + yoloversion + '_oa.png')
    plt.show()

    cm = pd.DataFrame(FPR_array, index=conf_threshold, columns=metric_threshold)
    fig, ax = plt.subplots(figsize=(8,8))
    sns.heatmap(cm, annot=True, fmt='.4f', ax=ax, cmap='Blues', square=True, vmin=0, vmax=1)
    ax.set_xlabel('\nIoU Threshold')
    ax.set_ylabel('Confidence Threshold\n')
    ax.figure.tight_layout()
    ax.figure.subplots_adjust(bottom=0.2)
    ax.invert_yaxis()
    plt.title('False Positive Rate')
    plt.savefig(r'F:\202105_PAI\data\P1_results\thresholds_' + yoloversion + '_FPR.png')
    plt.show()


    print('finished')
