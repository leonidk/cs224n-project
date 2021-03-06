
# coding: utf-8

from __future__ import print_function
from load_glove import *
import json
from collections import defaultdict
import numpy as np
import tensorflow as tf
from sys import argv
import os
import argparse
import gzip

parser = argparse.ArgumentParser()
parser.add_argument('--runmode', dest='runmode', choices=["train", "test"], default="train")
parser.add_argument('--dataset_name', dest='dataset_name', type=str, default="duc2004")
parser.add_argument('--trained_on', dest='trained_on', type=str, default="duc2004")
parser.add_argument('--cnn', dest='cnn', type=int, default=0)
parser.add_argument('--train_embedding', dest='train_embedding', type=int, default=0)
parser.add_argument('--output_root', dest='output_root', type=str, default="")
parser.add_argument('--evaluation_root', dest='evaluation_root', type=str, default="../../evaluation")
parser.add_argument('--l2', dest='l2', type=float, default=0.001)

args = parser.parse_args()

log_components = ["train"]
if args.runmode == "train":
    log_components += [args.dataset_name]
elif args.runmode == "test":
    log_components += [args.trained_on]
if args.cnn:
    log_components += ["cnn"]
if args.train_embedding:
    log_components += ["train_embedding"]
LOGDIR = "-".join(log_components)
if args.output_root != "":
    LOGDIR = os.path.join(args.output_root, LOGDIR)
dataset_file = os.path.join("../../data", args.dataset_name, "data.json")
print("Runmode %s on dataset %s" % (args.runmode, args.dataset_name))

if args.runmode == "test":
    predictions_dir_name = "_".join(["seq2seq", LOGDIR, args.trained_on, args.dataset_name])
    predictions_dir_path = os.path.join(args.evaluation_root, predictions_dir_name)
    predictions_file_path = os.path.join(predictions_dir_path, "prediction.json.gz")

GLOVE_LOC = '../../data/glove/glove.6B.100d.txt'

INPUT_MAX = 150
OUTPUT_MAX = 15
VOCAB_MAX = 10000

GLV_RANGE = 0.5
LR_DECAY_AMOUNT = 0.8
starter_learning_rate = 1e-2
hs = 256

batch_size = 32
PRINT_EVERY = 100
CHECKPOINT_EVERY = 5000
TRAIN_KEEP_PROB = 0.5
TRAIN_EMBEDDING = args.train_embedding
USE_CNN = args.cnn
KERNEL_SIZE = 7
concat_state = False
if args.runmode == "train":
    if not os.path.exists(LOGDIR):
        os.makedirs(LOGDIR)
if args.runmode == "test":
    if not os.path.exists(predictions_dir_path):
        os.makedirs(predictions_dir_path)

words = glove2dict(GLOVE_LOC)
word_counter = defaultdict(int)
GLV_DIM = words['the'].shape[0]
not_letters_or_digits = u'!"#%\'()*+,-./:;<=>?@[\]^_`{|}~'
translate_table = dict((ord(char), None) for char in not_letters_or_digits)
def clean(text,clip_n=0):
    res = text.replace('<d>','').replace('<p>','').replace('<s>','').replace('</d>','').replace('</p>','').replace('</s>','').translate(translate_table)
    r2 = []
    for word in res.split():
        if word not in words:
            words[word] = np.array([random.uniform(-GLV_RANGE, GLV_RANGE) for i in range(GLV_DIM)])
    for word in res.split():
        word_counter[word] += 1
    if clip_n > 0:
        return ' '.join(res.split()[:clip_n])
    else:
        return res

from collections import defaultdict
with open(dataset_file) as fp:
    data = json.load(fp)
    train_o = [x for x in data if x['set'] == 'train']
    dev_o = [x for x in data if x['set'] == 'dev']
    test_o = [x for x in data if x['set'] == 'test']

    train = sum([[(clean(x['data'],INPUT_MAX), clean(x['label'][i],OUTPUT_MAX),idx) for i in xrange(len(x['label']))] for idx,x in enumerate(train_o)],[])
    dev   = sum([[(clean(x['data'],INPUT_MAX), clean(x['label'][i],OUTPUT_MAX),idx) for i in xrange(len(x['label']))] for idx,x in enumerate(dev_o)  ],[])
    test  = sum([[(clean(x['data'],INPUT_MAX), clean(x['label'][i],OUTPUT_MAX),idx) for i in xrange(len(x['label']))] for idx,x in enumerate(test_o) ],[])

    valid_words = (sorted([(v,k) for k,v in word_counter.items()])[::-1])
    print(len(valid_words))
    valid_words = ['<PAD>'] + [x[1] for x in valid_words[:VOCAB_MAX]] + ['<EOS>','<UNK>','<SOS>']
    unk_idx = valid_words.index('<UNK>')
    vwd = defaultdict(lambda : unk_idx)
    for idx,word in enumerate(valid_words):
        vwd[word] = idx

    initial_matrix = np.array([words[x] for x in valid_words])
    def sent_to_idxs(sentence):
        base =  [vwd[word] for word in sentence.split()]
        sen_len = len(base)
        base =  [vwd['<SOS>']] + base# + [valid_words.index('<EOS>')]
        pad_word = (OUTPUT_MAX-sen_len)
        base = base + pad_word*[vwd['<EOS>']]
        if pad_word == 0:
            return base,(sen_len,pad_word) 
        else:
            return base,(sen_len+1,pad_word-1)
    def sent_to_idxs_nopad(sentence):
        base =  [vwd[word] for word in sentence.split()]
        return base
    random.shuffle(train)
    train_x = [sent_to_idxs_nopad(x[0]) for x in train]
    train_y = [sent_to_idxs(x[1])[0] for x in train]
    train_len = [sent_to_idxs(x[1])[1] for x in train]

    dev_x = [sent_to_idxs_nopad(x[0]) for x in dev]
    dev_y = [sent_to_idxs(x[1])[0] for x in dev]
    dev_len = [sent_to_idxs(x[1])[1] for x in dev]

    test_x = [sent_to_idxs_nopad(x[0]) for x in test]
    test_y = [sent_to_idxs(x[1])[0] for x in test]
    test_len = [sent_to_idxs(x[1])[1] for x in test]


def try_restoring_checkpoint(session, saver):
    print('trying to restore checkpoints...')
    try:
      ckpt_state = tf.train.get_checkpoint_state(LOGDIR)
    except tf.errors.OutOfRangeError as e:
      print('Cannot restore checkpoint: ', e)
      exit(1)

    if not (ckpt_state and ckpt_state.model_checkpoint_path):
      print('No model to eval yet at ', LOGDIR)
      return

    print('Loading checkpoint ', ckpt_state.model_checkpoint_path)
    saver.restore(session, ckpt_state.model_checkpoint_path)
    print('...loaded.')

tf.reset_default_graph()
global_step = tf.Variable(0, trainable=False)
VOCAB_SIZE = len(valid_words)
learning_rate = tf.train.exponential_decay(starter_learning_rate, global_step, len(train_x)/batch_size, LR_DECAY_AMOUNT, staircase=True)

input_placeholder = tf.placeholder(tf.int32)
mask_placeholder = tf.placeholder(tf.bool,(None,OUTPUT_MAX))
labels_placeholder = tf.placeholder(tf.int32,(None,OUTPUT_MAX+1))
dropout_rate = tf.placeholder(tf.float32,())

embedding = tf.Variable(initial_matrix,dtype=tf.float32,trainable=TRAIN_EMBEDDING)
input_embed = tf.nn.embedding_lookup(embedding,input_placeholder)
if USE_CNN:
    W1 = tf.get_variable("W1", shape=[KERNEL_SIZE,GLV_DIM,hs], initializer=tf.contrib.layers.xavier_initializer())
    b1 = tf.Variable(tf.constant(0.0, shape=[hs]))
    W2 = tf.get_variable("W2", shape=[1,hs,hs], initializer=tf.contrib.layers.xavier_initializer())
    b2 = tf.Variable(tf.constant(0.0, shape=[hs]))
    h_conv1 = tf.nn.tanh(tf.nn.conv1d(input_embed, W1, stride=1, padding='SAME') + b1)
    h_state =  tf.nn.conv1d(h_conv1, W2, stride=1, padding='SAME') + b2
    state = tf.reduce_max(h_state,1)
else:
    input_summed= tf.reduce_mean(input_embed,1)

    hh0 = tf.get_variable("hh0", shape=[GLV_DIM,hs], initializer=tf.contrib.layers.xavier_initializer(),dtype=tf.float32)
    hb0 = tf.Variable(tf.constant(0.0, shape=[hs],dtype=tf.float32))

num_layer = 1
#cell = tf.contrib.rnn.MultiRNNCell([tf.contrib.rnn.GRUCell(hs) for _ in xrange(num_layer)])
cell = tf.contrib.rnn.GRUCell(hs)

looked_up = tf.nn.embedding_lookup(embedding,labels_placeholder)
x = tf.reshape(looked_up,[-1,OUTPUT_MAX+1,GLV_DIM])

U = tf.get_variable("U", shape=(hs,VOCAB_SIZE), initializer=tf.contrib.layers.xavier_initializer())
b2 = tf.get_variable("b2", shape=(VOCAB_SIZE,), initializer=tf.constant_initializer(0.0))
if USE_CNN:
    pass
else:
    state = tf.matmul(input_summed,hh0) + hb0

duped_initial = tuple([state for _ in xrange(num_layer)])
if concat_state:
    dupe_state = tf.reshape(tf.tile(input_summed,[1,OUTPUT_MAX+1]),[-1,OUTPUT_MAX+1,GLV_DIM])
    x = tf.concat([x,dupe_state],1)

#outputs, states = tf.nn.dynamic_rnn(cell, x, initial_state=state,dtype=tf.float32)
preds = []
diffs = []

state = tf.zeros((tf.shape(labels_placeholder)[0],hs))#cell.zero_state() #(state,)
context = tf.reshape(input_summed,[-1,GLV_DIM])

correct_running_loss = context
with tf.variable_scope("RNN"):
    for time_step in range(OUTPUT_MAX):
        if time_step >= 1:
            tf.get_variable_scope().reuse_variables()
        x_in = x[:,time_step,:]
        #print(x_in.get_shape())
        x_in= tf.concat([x_in,context],1)
        #print(x_in.get_shape())
        output, state = cell(x_in, state)
        pred = tf.matmul(output,U) + b2
        #print(pred.get_shape())
        vocab_words = tf.argmax(pred,axis=1)
        look_up_out = tf.nn.embedding_lookup(embedding,vocab_words)
        #print(look_up_out.get_shape())
        correct_running_loss = correct_running_loss - x[:,time_step+1,:]
        context =  context - look_up_out
        diffs.append(tf.nn.l2_loss(look_up_out - x[:,time_step+1,:]))
        preds.append(pred)
        ### END YOUR CODE

preds = tf.stack(preds)
preds = tf.transpose(preds,perm=[1,0,2])

diffs = tf.stack(diffs)
#outputs_batchword = tf.reshape(outputs[:,:OUTPUT_MAX,:],[-1,hs])
#out_drop = tf.nn.dropout(outputs_batchword,dropout_rate)
#pred_batchword = tf.matmul(out_drop,U) + b2
#preds = tf.reshape(pred_batchword,[-1,OUTPUT_MAX,VOCAB_SIZE])

ce = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=preds,labels=labels_placeholder[:,1:])
loss = tf.reduce_mean(tf.boolean_mask(ce,mask_placeholder)) + args.l2*tf.reduce_mean(tf.boolean_mask(diffs,mask_placeholder))
tf.summary.scalar('loss', loss)

optimizer = tf.train.AdamOptimizer(learning_rate)
gvs = optimizer.compute_gradients(loss)
capped_gvs = [((tf.clip_by_value(grad, -1., 1.) if grad != None else None), var)  for grad, var in gvs]
train_step = optimizer.apply_gradients(capped_gvs,global_step=global_step)


def sample(context_vector):
    sentence = []
    for i in xrange(OUTPUT_MAX):
        x = sent_to_idxs(' '.join(sentence))[0]
        feed_dict = {
            input_placeholder: np.array(context_vector).reshape([1,-1]),
            labels_placeholder: np.array(x).reshape([1,-1]),
            dropout_rate: 1.0
        }
        probs = np.squeeze(preds.eval(feed_dict=feed_dict))
        new_word = valid_words[np.argmax(probs[i,:])]
        if new_word != '<EOS>':
            sentence.append(new_word)
        else:
            break
    return ' '.join(sentence)
def sample_batch(context_vectors):
    sizes = [len(x) for x in context_vectors]
    mat = np.zeros(shape=(len(context_vectors),INPUT_MAX))
    for idx,row in enumerate(context_vectors):
        mat[idx,:sizes[idx]] = np.array(row)
    num_sent = np.array(context_vectors).shape[0]
    sentences = [[] for _ in xrange(num_sent)]

    for i in xrange(OUTPUT_MAX):
        x = [sent_to_idxs(' '.join(s))[0] for s in sentences]
        feed_dict = {
            input_placeholder: mat.reshape([num_sent,-1]),
            labels_placeholder: np.array(x).reshape([num_sent,-1]),
            dropout_rate: 1.0
        }
        probs = preds.eval(feed_dict=feed_dict) # batch,word,vocab
        for batch_i,batch_prob in enumerate(probs):
            new_word = valid_words[np.argmax(batch_prob[i,:])]
            sentences[batch_i].append(new_word)
    stops = [sentence.index('<EOS>') if '<EOS>' in sentence else OUTPUT_MAX for sentence in sentences]
    return [' '.join(sentence[:maxe]) for maxe,sentence in zip(stops,sentences)]

with tf.Session() as sess:
    merged = tf.summary.merge_all()
    sess.run(tf.global_variables_initializer())
    summary_writer = tf.summary.FileWriter(LOGDIR, sess.graph)
    saver =  tf.train.Saver()
    try_restoring_checkpoint(sess, saver)
    data_size = len(train_x)
    if args.runmode == "train":

        for i in range(data_size*10):
            start_idx = (i*batch_size)%data_size
            end_idx = start_idx+batch_size
            mask = np.array([np.array([True]*x[0] + [False]*x[1]) for x in train_len[start_idx:end_idx]])
            train_sizes = [len(x) for x in train_x[start_idx:end_idx]]
            train_mat = np.zeros(shape=(len(train_sizes),max(train_sizes)))
            for idx,row in enumerate(train_x[start_idx:end_idx]):
                train_mat[idx,:train_sizes[idx]] = np.array(row)
            feed_dict = {
                input_placeholder: train_mat,
                labels_placeholder: train_y[start_idx:end_idx],
                mask_placeholder: mask,
                dropout_rate: TRAIN_KEEP_PROB
            }
            _, bl, summary = sess.run([train_step, loss, merged], feed_dict=feed_dict)
            if args.runmode == "train":
                summary_writer.add_summary(summary, i)
            if i % PRINT_EVERY == 0:
                print(i,bl)
                print('TRAIN_SAMPLE: ',sample(train_x[start_idx]))
                print('TRAIN_LABEL: ',' '.join([x for x in [valid_words[x] for x in train_y[start_idx]] if x not in ['<EOS>','<SOS>']]))
                index = int(random.random()*(len(dev_y)-1))
                print('DEV_SAMPLE: ',sample(dev_x[index]))
                print('DEV_LABEL: ',' '.join([x for x in [valid_words[x] for x in dev_y[index]] if x not in ['<EOS>','<SOS>']]))
                print('\n')
            if i != 0 and i %2000*len(train_x)/batch_size == 0:
                print("Saving checkpoint...")
                saver.save(sess, os.path.join(LOGDIR, 'model-checkpoint-'), global_step=i)
    if args.runmode == "test":
        batch_size = 2048
        orig_file = train_o
        x_file = train_x
        src_file = train

        print("Running predictions for %d data points..." % len(x_file))
        predictions = []
        seen_data = {}
        for batch_i in xrange(0,len(x_file),batch_size):
            evaluation_data = x_file[batch_i:batch_i+batch_size]
            prediction_results = sample_batch(evaluation_data)
            for i, prediction in enumerate(prediction_results):
                if src_file[i][2] not in seen_data:
                    orig_data = orig_file[src_file[i][2]]
                    orig_data['prediction'] = "<d> <p> <s> " + prediction + " </s> </p> </d>"
                    predictions.append(orig_data)
                    seen_data[src_file[i][2]] = 1
        print("Done, writing json.gz file...")
        with gzip.open(predictions_file_path, 'w') as predictions_file:
            json.dump(predictions, predictions_file, sort_keys=True, indent=4, separators=(',', ': '))
        print("All done.")
