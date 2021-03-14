# -*- coding: utf-8 -*-
"""ES0221.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/18DnIG9BkOBIVh_HWhridGH82jOCGejx3
"""

import gzip
import pandas as pd
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import numpy as np
import torch.nn.functional as F
from torch import optim
from os import listdir
import math

pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1000)

def parse_header_of_csv(user_context_data):

    columns = list(user_context_data.columns)

    # The first column should be timestamp:
    assert columns[0] == 'timestamp';
    # The last column should be label_source:
    assert columns[-1] == 'label_source';

    # Search for the column of the first label:
    for (ci,col) in enumerate(columns):
        if col.startswith('label:'):
            first_label_ind = ci;
            break;
        pass;

    # Feature columns come after timestamp and before the labels:
    feature_names = columns[1:first_label_ind]
    # Then come the labels, till the one-before-last column:
    label_names = columns[first_label_ind:-1]

    for (li,label) in enumerate(label_names):
        # In the CSV the label names appear with prefix 'label:', but we don't need it after reading the data:
        assert label.startswith('label:')
        label_names[li] = label.replace('label:','')
        pass

    return (feature_names,label_names);

def parse_body_of_csv(user_context_data,n_features):

    # Read the entire CSV body into a single numeric matrix:
    # full_table = np.loadtxt(StringIO.StringIO(csv_str),delimiter=',',skiprows=1);

    # Timestamp is the primary key for the records (examples):
    timestamps = user_context_data['timestamp']

    # Read the sensor features:
    X = user_context_data.iloc[:,1:(n_features+1)]

    # Read the binary label values, and the 'missing label' indicators:
    # trinary_labels_mat = user_context_data[:,(n_features+1):-1]; # This should have values of either 0., 1. or NaN
    # M = np.isnan(trinary_labels_mat); # M is the missing label matrix
    # Y = np.where(M,0,trinary_labels_mat) > 0.; # Y is the label matrix

    Y = user_context_data.iloc[:,(n_features+1):-1]; # This should have values of either 0., 1. or NaN
    M = np.isnan(Y)

    return (X,Y,M,timestamps)

def read_user_data(source_dir, uuid):

    user_data_file = source_dir + '%s.features_labels.csv.gz' % uuid

    user_context_data = \
            pd.read_csv(user_data_file, compression='gzip', header=0, sep=',', \
                quotechar='"', error_bad_lines=False)

    (feature_names,label_names) = parse_header_of_csv(user_context_data)
    n_features = len(feature_names)
    (X,Y,M,timestamps) = parse_body_of_csv(user_context_data,n_features)

    return (X,Y,M,timestamps,feature_names,label_names)

def read_data_from_all_users(source_dir='../../ESDataset/'):

    users = listdir(source_dir)
    users = [user.split('.', 1)[0] for user in users]

    print("There are ", len(users), " users")

    X = pd.DataFrame()
    Y = pd.DataFrame()
    timestamps = pd.DataFrame()
    M = pd.DataFrame()

    for i in range(len(users)):
        uuid = users[i]
        (X_user, Y_user, timestamp_user, M_user, feature_names, label_names) =\
                                                read_user_data(source_dir, uuid)
        X = X.append(X_user)
        Y = Y.append(Y_user)
        timestamps = timestamps.append(timestamp_user)
        M = M.append(M_user)

        if i % 5 == 0:
            print("Read %d users data" %i)

    return users, X, Y, M, timestamps, label_names

users, X, Y, M, timestamps, label_names = \
        read_data_from_all_users(source_dir='../../ESDataset/')

def get_class_weights(y_train, M):

   pos_weights = torch.sum(y_train, dim=0)
   missing_counts = torch.sum(M, dim=0)

   tensor = torch.ones((1,), dtype=torch.float64)
   neg_weights = tensor.new_full((1, len(y_train[0])), len(y_train)) - \
                            pos_weights - missing_counts

   pos_weights = (len(y_train) - missing_counts) / pos_weights
   neg_weights = (len(y_train) - missing_counts) / neg_weights

   pos_weights = y_train * pos_weights
   neg_weights = np.logical_not(y_train) * neg_weights

   pos_weights[np.isnan(pos_weights)] = 0.
   neg_weights[np.isnan(neg_weights)] = 0.

   instance_weights = (pos_weights + neg_weights) * np.logical_not(M)

   return instance_weights

def myLoss(model, predicts, y_output, instance_weights):

    criterion = nn.BCEWithLogitsLoss(reduction='none')
    loss = criterion(predicts, y_output)
    loss = loss * instance_weights

    l1_reg = torch.tensor(0., requires_grad=True)
    l1_lambda = 5e-4
    for name, param in model.named_parameters():
        if 'weight' in name:
            l1_reg = l1_reg + torch.norm(param, 1)

    loss = torch.mean(loss) + (l1_lambda * l1_reg)
    return loss

# Classifier
class Network(nn.Module):

    def __init__(self):

        super(Network, self).__init__()
        # Inputs to hidden layer linear transformation
        self.h1 = nn.Linear(176, 16)
        self.activation = nn.LeakyReLU()
        self.h2 = nn.Linear(16, 16)
        self.output = nn.Linear(16, 51)

    def forward(self, x):

        # Hidden layer with Leaky ReLU activation
        x = self.activation(self.h1(x))
        x = self.activation(self.h2(x))

        # Output layer
        x = self.output(x)
        return x

criterion = nn.BCEWithLogitsLoss()
train_losses, test_losses = [], []
accuracies = []
epochs = 40
batch_size = 300

#Train model
def trainAndTestModel(model, X_train, y_train, X_test, y_test, train_weights):

    optimizer = optim.SGD(model.parameters(), lr=0.01, momentum=0.5)
    lre = 0.1
    trainLoaderSize = len(X_train)/batch_size

    for e in range(epochs):

        running_loss = 0
        permutation = torch.randperm(X_train.size()[0])

        for param_group in optimizer.param_groups:
            param_group['lr'] = lre

        for i in range(0, X_train.size()[0], batch_size):

            optimizer.zero_grad()

            # indices = permutation[i:i+batch_size]
            # batch_x, batch_y = X_train[indices], y_train[indices]
            # batch_weights = train_weights[indices]

            batch_x, batch_y = X_train[i:i+batch_size], y_train[i:i+batch_size]
            batch_weights = train_weights[i:i+batch_size]

            log_ps = model(batch_x)
            # loss = myLoss(model, log_ps.squeeze(), batch_y, batch_weights)
            loss = criterion(log_ps.squeeze(), batch_y)
            loss.backward()
            optimizer.step()

            running_loss += loss.item()

        else:

            train_losses.append(running_loss/trainLoaderSize)
            print("Epoch: {}/{}".format(e+1, epochs))
            print("Training Loss: {:.3f}".format(running_loss/trainLoaderSize))

            ba, sen, spec, pre, f_score, test_loss \
            = validateModel(model, X_test, y_test)

        lre = lre - ((0.1 - 0.01) /epochs)

    return ba, sen, spec, pre, f_score, test_loss

def validateModel(model, X_test, y_test):

        accuracy = 0.
        running_test_loss = 0.
        tp = 0.
        fp = 0.
        tn = 0.
        fn = 0.

        testLoaderSize = len(X_test)/batch_size
        for i in range(0, X_test.size()[0], batch_size):

            # Turn off gradients for validation, saves memory and computations
            with torch.no_grad():

                model.eval()
                batch_x, batch_y = X_test[i:i+batch_size], y_test[i:i+batch_size]
                log_ps = model(batch_x)
                loss = criterion(log_ps.squeeze(), batch_y)
                running_test_loss += loss.item()

                probs = torch.sigmoid(log_ps).cpu()
                probs = np.round(probs)

                # Naive accuracy (correct classification rate):
                accuracy += torch.mean((probs == batch_y).float())

                # Ground - 1 0 1 0 1
                # Actual - 0 0 1 0 1

                # Count occorrences of true-positive, true-negative, false-positive, and false-negative:
                tp += torch.sum(np.logical_and(probs, batch_y));
                tn += torch.sum(np.logical_and(np.logical_not(probs),np.logical_not(batch_y)));
                fp += torch.sum(np.logical_and(probs,np.logical_not(batch_y)));
                fn += torch.sum(np.logical_and(np.logical_not(probs),batch_y));

        model.train()

        # Sensitivity (=recall=true positive rate) and Specificity (=true negative rate):
        sensitivity = float(tp) / (tp+fn);
        specificity = float(tn) / (tn+fp);

        # Balanced accuracy is a more fair replacement for the naive accuracy:
        balanced_accuracy = (sensitivity + specificity) / 2.;

        # Precision:
        # Beware from this metric, since it may be too sensitive to rare labels.
        # In the ExtraSensory Dataset, there is large skew among the positive and negative classes,
        # and for each label the pos/neg ratio is different.
        # This can cause undesirable and misleading results when averaging precision across different labels.
        precision = float(tp) / (tp+fp);

        f_score = (2 * precision * sensitivity) / (precision + sensitivity)

        test_loss = running_test_loss / testLoaderSize

        print("-"*10);
        print('Accuracy*:         %.2f' % (accuracy/testLoaderSize));
        print('Sensitivity (TPR): %.2f' % sensitivity);
        print('Specificity (TNR): %.2f' % specificity);
        print('Balanced accuracy: %.2f' % balanced_accuracy);
        print('Precision**:       %.2f' % precision);
        print('F1 Score:          %.2f' % f_score);

        test_losses.append(test_loss)
        accuracies.append(balanced_accuracy)

        print("Test Loss: {:.3f} ".format(test_loss))
        print("-"*10);

        #      "Test Accuracy: {:.3f}".format(accuracy/testLoaderSize))

        return balanced_accuracy, sensitivity, specificity, precision, f_score,\
                    test_loss

def plot_loss(train_loss, test_loss, title, x_axis, y_axis):

    plt.plot(train_loss, '-b', label='train_loss')
    plt.plot(test_loss, '-r', label='test_loss')

    plt.xlabel(x_axis)
    plt.ylabel(y_axis)

    plt.legend(loc='upper right')
    plt.title(title)

    # plt.show()

def plot_balanced_accuracy(accuracy_values, title = 'iterations'):

    plt.xlabel(title)
    plt.ylabel('BA')
    plt.title('Balanced Accuracy')
    plt.plot(accuracy_values)
    # plt.show()

def plot_labelled_metrics(labelled_examples, accuracy_values, sensitivity_values,
                     specificity_values, precision_values, f_score_values, \
                      plot_label, title = 'Metrics',):

    plt.plot(labelled_examples, accuracy_values, label='BA')
    plt.plot(labelled_examples, sensitivity_values, label='Sensitivity')
    plt.plot(labelled_examples, specificity_values, label='Specificity')
    plt.plot(labelled_examples, precision_values, label='Precision')
    plt.plot(labelled_examples, f_score_values, label='F-Score')
    plt.xlabel('labelled_examples')
    plt.ylabel('Metrics')
    plt.legend(loc="upper right")
    plt.title(title)

def plot_labelled_ba(labelled_examples, accuracy_values,
                      plot_label, title = 'Balanced Accuracy',):

    plt.plot(labelled_examples, accuracy_values, label=plot_label)
    plt.xlabel('labelled_examples')
    plt.ylabel('Metrics')
    plt.legend(loc="upper right")
    plt.title(title)

def estimate_standardization_params(X_train):
    mean_vec = np.nanmean(X_train,axis=0);
    std_vec = np.nanstd(X_train,axis=0);
    return (mean_vec,std_vec);

def standardize_features(X,mean_vec,std_vec):
    # Subtract the mean, to centralize all features around zero:
    X_centralized = X - mean_vec.reshape((1,-1));
    # Divide by the standard deviation, to get unit-variance for all features:
    # * Avoid dividing by zero, in case some feature had estimate of zero variance
    normalizers = np.where(std_vec > 0., std_vec, 1.).reshape((1,-1));
    X_standard = X_centralized / normalizers;
    return X_standard;

def sample_unlabelled_data(model, X_unlabelled, sample_strategy='random'):

    sample_indices = 0

    if sample_strategy == 'random':

        sample_indices = torch.randperm(X_unlabelled.size()[0])

    elif sample_strategy == 'avg_prob':

        confidence_list = []
        model.eval()

        # Turn off gradients for validation, saves memory and computations
        with torch.no_grad():

            for i, item in enumerate(X_unlabelled):

                # batch_x = X_unlabelled[i:i+batch_size]
                log_ps = model(item)
                probs = torch.exp(log_ps).cpu()
                avg_prob = torch.mean(probs)

                conf = []
                conf.append(i)
                conf.append(avg_prob)
                confidence_list.append(conf)

        confidence_list.sort(key=lambda x: x[1])
        sample_indices = torch.tensor([item[0] for item in confidence_list], \
                                      dtype = torch.long)

    elif sample_strategy == 'max_margin':

        confidence_list = []
        model.eval()

        # Turn off gradients for validation, saves memory and computations
        with torch.no_grad():

            for i, item in enumerate(X_unlabelled):

                # batch_x = X_unlabelled[i:i+batch_size]
                log_ps = model(item)
                probs = torch.exp(log_ps).cpu()
                sort_distances = torch.sort(probs, dim=0, descending = True)
                max_margin = sort_distances[0][0] - sort_distances[0][1]
                max_margin = 1 - max_margin

                conf = []
                conf.append(i)
                conf.append(max_margin)
                confidence_list.append(conf)

        confidence_list.sort(key=lambda x: x[1])
        sample_indices = torch.tensor([item[0] for item in confidence_list], \
                                      dtype = torch.long)
    elif sample_strategy == 'lcp':

        confidence_list = []
        model.eval()

        # Turn off gradients for validation, saves memory and computations
        with torch.no_grad():

            for i, item in enumerate(X_unlabelled):

                # batch_x = X_unlabelled[i:i+batch_size]
                log_ps = model(item)
                probs = torch.exp(log_ps).cpu()
                simple_least_conf = torch.max(probs)
                num_labels = probs.numel() # number of labels
                lcp = (1 - simple_least_conf) * (num_labels / (num_labels -1))

                conf = []
                conf.append(i)
                conf.append(lcp)
                confidence_list.append(conf)

        confidence_list.sort(key=lambda x: x[1])
        sample_indices = torch.tensor([item[0] for item in confidence_list], \
                                      dtype = torch.long)

    elif sample_strategy == 'entropy':

        confidence_list = []
        model.eval()

        # Turn off gradients for validation, saves memory and computations
        with torch.no_grad():

            for i, item in enumerate(X_unlabelled):

                # batch_x = X_unlabelled[i:i+batch_size]
                log_ps = model(item)
                probs = torch.exp(log_ps).cpu()
                log_probs = probs * torch.log2(probs)
                raw_entropy = 0 - torch.sum(log_probs)

                normalized_entropy = raw_entropy / math.log(probs.numel(), 2)

                conf = []
                conf.append(i)
                conf.append(normalized_entropy.item())
                confidence_list.append(conf)

        confidence_list.sort(key=lambda x: x[1], reverse=True)
        sample_indices = torch.tensor([item[0] for item in confidence_list], \
                                      dtype = torch.long)

    return sample_indices

# Strategies can be random or classwise
def test_train_split(X, Y, M, percentage = 0.7, strategy = 'random'):

    if strategy == 'classwise':
        pass
    else:
        sample_indices = np.random.permutation(len(X)).tolist()
        test_length = (int)((1-percentage) * len(X))

        X_test = torch.tensor(X.iloc[sample_indices[:test_length]].values, \
                    dtype=torch.float32)
        (mean_vec,std_vec) = estimate_standardization_params(X_test);
        X_test = standardize_features(X_test,mean_vec,std_vec);
        y_test = torch.tensor(Y.iloc[sample_indices[:test_length]].values, \
                    dtype=torch.float32)

        M_test = torch.tensor(M.iloc[sample_indices[:test_length]].values, \
                    dtype=torch.float32)

        X.reset_index(inplace=True)
        Y.reset_index(inplace=True)
        M.reset_index(inplace=True)

        X.drop(X.index[sample_indices[:test_length]], inplace = True)
        Y.drop(Y.index[sample_indices[:test_length]], inplace = True)
        M.drop(M.index[sample_indices[:test_length]], inplace = True)

        del X['index']
        del Y['index']
        del M['index']

        X_train = torch.tensor(X.values, dtype=torch.float32)
        (mean_vec,std_vec) = estimate_standardization_params(X_train);
        X_train = standardize_features(X_train,mean_vec,std_vec);
        y_train = torch.tensor(Y.values, dtype=torch.float32)

        M_train = torch.tensor(M.values.astype(np.float32))

        return X_train, y_train, X_test, y_test, M_train, M_test

def train_without_AL(X_train, y_train, X_test, y_test, M_train, M_test, model):

    instance_weights_train = get_class_weights(y_train, M_train)

    # Train and Evaluate Model
    trainAndTestModel(model, X_train.float(), y_train, X_test.float(), y_test, \
                      instance_weights_train)

    plot_loss(train_losses, test_losses, 'Loss', 'iterations', 'loss')
    plt.savefig('Full_supervised_loss.png')
    plt.clf()
    plot_balanced_accuracy(accuracies, 'BA without AL')
    plt.savefig('Full_supervised_ba.png')
    plt.clf()

# Prepare training and test data
M = np.isnan(Y)

# Also, there may be missing sensor-features (represented in the data as NaN).
# You can handle those by imputing a value of zero (since we standardized, this is equivalent to assuming average value).
# You can also further select examples - only those that have values for all the features.
# For this tutorial, let's use the simple heuristic of zero-imputation:
X = X.iloc[:, np.r_[0:52, 83:181, 183:209]]
X[np.isnan(X)] = 0.
Y[np.isnan(Y)] = 0.

X_train, y_train, X_test, y_test, M_train, M_test = test_train_split(X, Y, M)

model_full_supervised = Network()
train_without_AL(X_train, y_train, X_test, y_test, M_train, M_test, model_full_supervised)

X_unlabelled = X_train
y_unlabelled = y_train
M_unlabelled = M_train

def describe_data(Y, iter):
    n_examples_per_label = torch.sum(Y,dim=0);
    labels_and_counts = zip(label_names,n_examples_per_label);
    # sorted_labels_and_counts = sorted(labels_and_counts,reverse=True,key=lambda pair:pair[1]);
    # print("How many examples does this user have for each contex-label:")
    # print("-"*20)
    # for (label,count) in sorted_labels_and_counts:
    #     print("label %s - %d minutes" % (label,count))
    #     pass;

    plt.figure(figsize=(20,10))
    plt.bar(label_names, n_examples_per_label, str(n_examples_per_label))

def stratify_samples(y_unlabelled_AL, sample_indices, threshold):

    y_unlabelled_AL = y_unlabelled_AL[sample_indices]
    stratified_sample_indicies = set()
    num_of_labels = y_unlabelled_AL.size()[1]

    for label in range(num_of_labels):

        examples = 0
        for i, y in enumerate(y_unlabelled_AL):
            if examples < threshold and y[label] == 1:
                stratified_sample_indicies.add(i)
                examples += 1

    rest_of_indices = []

    for i in sample_indices:

        if i in stratified_sample_indicies:
            continue
        else:
            rest_of_indices.append(i)

    return list(stratified_sample_indicies), rest_of_indices

def train_AL(X_unlabelled, y_unlabelled, X_test, y_test, M_unlabelled, M_test ,\
             model, sample_strategy = 'random', threshold = 10, sample_size=50):

    X_train =  torch.zeros(0)
    y_train =  torch.zeros(0)
    M_train = torch.zeros(0)

    # X_unlabelled_AL =  torch.zeros(0)
    # y_unlabelled_AL =  torch.zeros(0)
    # M_unlabelled_AL = torch.zeros(0)

    iter = 1

    AL_accuracies, AL_test_loss, labelled_examples = [], [], []
    AL_Sen, AL_Spec, AL_precision, AL_f_score = [], [], [], []

    labelled_size = 0
    # unlabelled_size = 0
    default_sample_size = sample_size

    # while X_train.size()[0] < X_unlabelled.size()[0]:
    while labelled_size < 20000:

        print("Active Learning Iteration: ", iter, "Sampling strategy: ", sample_strategy)

        # X_unlabelled_AL = torch.cat((X_unlabelled_AL, \
        #                         X_unlabelled[unlabelled_size:unlabelled_size+5000]), 0)
        # y_unlabelled_AL = torch.cat((y_unlabelled_AL, \
        #                         y_unlabelled[unlabelled_size:unlabelled_size+5000]), 0)
        # M_unlabelled_AL = torch.cat((M_unlabelled_AL, \
        #                         M_unlabelled[unlabelled_size:unlabelled_size+5000]), 0)
        #
        # unlabelled_size += 5000

        sample_indices = sample_unlabelled_data(model, X_unlabelled.float(), sample_strategy)

        # if iter == 1:

        strat_sample_indices, rest_of_indices = stratify_samples(y_unlabelled, sample_indices, threshold)
        # print(torch.sum(y_unlabelled_AL[sample_indices[:sample_size]],dim=0))
        sample_indices = strat_sample_indices + rest_of_indices

        if len(strat_sample_indices) > default_sample_size:
            sample_size = len(strat_sample_indices)
        else:
            sample_size = default_sample_size

        X_train = torch.cat((X_train, X_unlabelled[sample_indices[:sample_size]]), 0)
        y_train = torch.cat((y_train, y_unlabelled[sample_indices[:sample_size]]), 0)
        M_train = torch.cat((M_train, M_unlabelled[sample_indices[:sample_size]]), 0)

        labelled_size += sample_size

        # print(len(X_train))

        describe_data(y_train, iter)
        plt.savefig(sample_strategy + '_' + str(iter)+'.png')
        plt.clf()

        X_unlabelled = X_unlabelled[sample_indices[sample_size:]]
        y_unlabelled = y_unlabelled[sample_indices[sample_size:]]

        M_unlabelled = M_unlabelled[sample_indices[sample_size:]]

        instance_weights_train = get_class_weights(y_train, M_train)

        # Train and Evaluate Model
        ba, sen, spec, pre, f_score, test_loss = trainAndTestModel(model, \
                                        X_train.float(), y_train, \
                                          X_test.float(), y_test, \
                      instance_weights_train)

        AL_accuracies.append(ba)
        AL_Sen.append(sen)
        AL_Spec.append(spec)
        AL_precision.append(pre)
        AL_f_score.append(f_score)
        AL_test_loss.append(test_loss)
        labelled_examples.append(X_train.size()[0])

        print("Total number of labelled samples %d" % (X_train.size()[0]))

        iter = iter + 1

    return labelled_examples, AL_accuracies, AL_Sen, AL_Spec, AL_precision, \
    AL_f_score


# Create model params and config
model_avg_prob = Network()
#
# labelled_examples, ba_avg_prob, sen_avg_prob, spec_avg_prob, precision_avg_prob, \
#                     f_score_avg_prob = train_AL_validate_unlabelled(X, Y, M, \
#                                           model = model_avg_prob, \
#                                           sample_strategy = 'avg_prob', \
#                                           sample_size=1000)
#
# labelled_examples, ba_avg_prob, sen_avg_prob, spec_avg_prob, precision_avg_prob, \
#                     f_score_avg_prob = train_AL(X_unlabelled, y_unlabelled, X_test, \
#                                         y_test, M_unlabelled, M_test,
#                                         model = model_avg_prob, \
#                                         sample_strategy = 'avg_prob', \
#                                         sample_size=1000)
#
# plot_labelled_metrics(labelled_examples, ba_avg_prob, sen_avg_prob, spec_avg_prob, \
#                       precision_avg_prob, f_score_avg_prob, plot_label='Avg_prob')
#
# plt.savefig('AL_validate_unlabelled_avg_prob.png')
# plt.clf()


# Create model params and config
model_random = Network()

# labelled_examples, ba_random, sen_random, spec_random, precision_random, \
#                 f_score_random = train_AL_validate_unlabelled(X, Y, M, model = model_random, \
#                                         sample_strategy = 'random', \
#                                         sample_size=1000)

labelled_examples, ba_random, sen_random, spec_random, precision_random, \
                f_score_random = train_AL(X_unlabelled, y_unlabelled, X_test, \
                                        y_test, M_unlabelled, M_test,
                                        model = model_random, \
                                        sample_strategy = 'random', \
                                        sample_size=500)

plot_labelled_metrics(labelled_examples, ba_random, sen_random, spec_random, \
                      precision_random, f_score_random, plot_label='Random')

plt.savefig('AL_validate_unlabelled_random.png')
plt.clf()

model_max_margin = Network()

# labelled_examples, ba_min_max = train_AL(X, Y, M, model = model_min_max, \
#                                          sample_strategy = 'max_margin', \
#                                          sample_size=1000)

# labelled_examples, ba_max, sen_max, spec_max, precision_max, \
#                 f_score_max = train_AL(X_unlabelled, y_unlabelled, X_test, \
#                                         y_test, M_unlabelled, M_test,
#                                         model = model_max_margin, \
#                                         sample_strategy = 'max_margin', \
#                                         sample_size=1000)
#
# plot_labelled_metrics(labelled_examples, ba_max, sen_max, spec_max, precision_max, \
#                 f_score_max, plot_label='Max_Margin')
#
# plt.savefig('AL_validate_unlabelled_max.png')
# plt.clf()

# Create model params and config
model_lcp = Network()

# labelled_examples, ba_lpc, sen_lpc, spec_lpc, precision_lpc, \
#                 f_score_lpc = train_AL_validate_unlabelled(X, Y, M, model = model_random, \
#                                         sample_strategy = 'random', \
#                                         sample_size=1000)

# labelled_examples, ba_lcp, sen_lcp, spec_lcp, precision_lcp, \
#                 f_score_lcp = train_AL(X_unlabelled, y_unlabelled, X_test, \
#                                         y_test, M_unlabelled, M_test,
#                                         model = model_lcp, \
#                                         sample_strategy = 'lcp', \
#                                         sample_size=1000)
#
# plot_labelled_metrics(labelled_examples, ba_lcp, sen_lcp, spec_lcp, \
#                       precision_lcp, f_score_lcp, plot_label='LCP')
#
# plt.savefig('AL_validate_unlabelled_lcp.png')
# plt.clf()

model_entropy = Network()

# labelled_examples, ba_lpc, sen_lpc, spec_lpc, precision_lpc, \
#                 f_score_lpc = train_AL_validate_unlabelled(X, Y, M, model = model_random, \
#                                         sample_strategy = 'random', \
#                                         sample_size=1000)

labelled_examples, ba_entropy, sen_entropy, spec_entropy, precision_entropy, \
                f_score_entropy = train_AL(X_unlabelled, y_unlabelled, X_test, \
                                        y_test, M_unlabelled, M_test,
                                        model = model_entropy, \
                                        sample_strategy = 'entropy', \
                                        sample_size=500)

plot_labelled_metrics(labelled_examples, ba_entropy, sen_entropy, spec_entropy, \
                      precision_entropy, f_score_entropy, plot_label='Entropy')

plt.savefig('AL_validate_unlabelled_entropy.png')
plt.clf()

# plot_labelled_ba(labelled_examples, ba_random, plot_label='Random')
# plot_labelled_ba(labelled_examples, ba_avg_prob, plot_label='Avg_prob')
# plot_labelled_ba(labelled_examples, ba_max, plot_label='Max_Margin')
# plot_labelled_ba(labelled_examples, ba_lcp, plot_label='LCP')
# plot_labelled_ba(labelled_examples, ba_entropy, plot_label='Entropy')
#
# plt.savefig('AL_validate_unlabelled.png')
# plt.clf()

def train_AL_validate_unlabelled(X, Y, M,\
             model, sample_strategy = 'random', sample_size=50):

    M = np.isnan(Y)

    X[np.isnan(X)] = 0.
    Y[np.isnan(Y)] = 0.

    X_unlabelled = torch.tensor(X.values, dtype=torch.float32)
    (mean_vec,std_vec) = estimate_standardization_params(X_unlabelled);
    X_unlabelled = standardize_features(X_unlabelled, mean_vec, std_vec);
    y_unlabelled = torch.tensor(Y.values, dtype=torch.float32)

    M_unlabelled = torch.tensor(M.values, dtype=torch.float32)

    X_train =  torch.zeros(0)
    y_train =  torch.zeros(0)
    M_train = torch.zeros(0)

    iter = 1

    AL_accuracies, AL_test_loss, labelled_examples = [], [], []
    AL_Sen, AL_Spec, AL_precision, AL_f_score = [], [], [], []

    stopping_size = (int)(0.3 * len(X))

    while X_unlabelled.size()[0] > stopping_size:
    # while iter < 3:

        print("Active Learning Iteration: ", iter, "Sample Strategy", sample_strategy)
        iter = iter + 1

        sample_indices = sample_unlabelled_data(model, X_unlabelled.float(), sample_strategy)

        X_train = torch.cat((X_train, X_unlabelled[sample_indices[:sample_size]]), 0)
        y_train = torch.cat((y_train, y_unlabelled[sample_indices[:sample_size]]), 0)
        M_train = torch.cat((M_train, M_unlabelled[sample_indices[:sample_size]]), 0)

        X_unlabelled = X_unlabelled[sample_indices[sample_size+1:]]
        y_unlabelled = y_unlabelled[sample_indices[sample_size+1:]]

        M_unlabelled = M_unlabelled[sample_indices[sample_size+1:]]

        print("Length of test set: ", len(X_unlabelled))

        # weights = calcWeights(y_train)
        # criterion = nn.BCEWithLogitsLoss(pos_weight=weights)
        instance_weights_train = get_class_weights(y_train, M_train)

        # Train and Evaluate Model
        ba, sen, spec, pre, f_score, test_loss = trainAndTestModel(model, \
                                        X_train.float(), y_train, \
                                          X_unlabelled.float(), y_unlabelled, \
                      instance_weights_train)

        AL_accuracies.append(ba)
        AL_Sen.append(sen)
        AL_Spec.append(spec)
        AL_precision.append(pre)
        AL_f_score.append(f_score)
        AL_test_loss.append(test_loss)
        labelled_examples.append(X_train.size()[0])

        print("Total number of labelled samples %d" % (X_train.size()[0]))

    return labelled_examples, AL_accuracies, AL_Sen, AL_Spec, AL_precision, \
    AL_f_score

def train_AL_per_user(num_of_users = 5):

    for i in range(num_of_users):

        print('************************* USER ' + str(i) + '***********************')

        uuid = users[i]
        X = pd.DataFrame()
        Y = pd.DataFrame()
        timestamps = pd.DataFrame()
        M = pd.DataFrame()

        (X_temp,Y_temp,timestamp_temp,M_temp,feature_names,label_names) = read_user_data(uuid)
        X = X.append(X_temp)
        Y = Y.append(Y_temp)
        timestamps = timestamps.append(timestamp_temp)
        M = M.append(M_temp)

        M = np.isnan(Y)
        X[np.isnan(X)] = 0.
        Y[np.isnan(Y)] = 0.

        X_unlabelled, y_unlabelled, X_test, y_test, M_unlabelled, M_test = \
                                                    test_train_split(X, Y, M)

        model_random = Network()

        labelled_examples, ba_random, sen_random, spec_random, precision_random, \
                f_score_random = train_AL(X_unlabelled, y_unlabelled, X_test, \
                                        y_test, M_unlabelled, M_test,
                                        model = model_random, \
                                        sample_strategy = 'random', \
                                        sample_size=1000)

        plot_labelled_metrics(labelled_examples, ba_random, sen_random, spec_random, \
                      precision_random, f_score_random, plot_label='Random')

        plt.savefig(str(i) + '_random.png')
        plt.clf()

        model_avg_prob = Network()

        labelled_examples, ba_avg_prob, sen_avg_prob, spec_avg_prob, precision_avg_prob, \
                    f_score_avg_prob = train_AL(X_unlabelled, y_unlabelled, X_test, \
                                        y_test, M_unlabelled, M_test,
                                        model = model_avg_prob, \
                                        sample_strategy = 'avg_prob', \
                                        sample_size=1000)

        plot_labelled_metrics(labelled_examples, ba_avg_prob, sen_avg_prob, spec_avg_prob, \
                              precision_avg_prob, f_score_avg_prob, plot_label='Avg_prob')

        plt.savefig(str(i) + '_avg_prob.png')
        plt.clf()

        plot_labelled_ba(labelled_examples, ba_random, plot_label='Random')
        plot_labelled_ba(labelled_examples, ba_avg_prob, plot_label='Avg_prob')

        plt.savefig(str(i) + '_random_vs_avg.png')
        plt.clf()

# train_AL_per_user(5)
