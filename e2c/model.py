
import tensorflow as tf

from .input import get_constants, load_inverse_vocab

def basic_cell(args, i):
	c = tf.contrib.rnn.BasicLSTMCell(
		args['num_units'],
		forget_bias=args['forget_bias'])
	c = tf.contrib.rnn.DropoutWrapper(
		cell=c, input_keep_prob=(1.0 - args['dropout']))

	if i > 1:
		c = tf.contrib.rnn.ResidualWrapper(c)

	return c

def model_fn(features, labels, mode, params):

	# --------------------------------------------------------------------------
	# Extra hyper parameters
	# --------------------------------------------------------------------------
	
	num_units = params["num_units"]
	forget_bias = 0
	dropout = params["dropout"]
	dtype = tf.float32
	beam_width = 0
	time_major = True
	vocab_const = get_constants(params)

	# --------------------------------------------------------------------------
	# Format inputs
	# --------------------------------------------------------------------------

	# embed words
	word_embedding = tf.get_variable("word_embedding", [params["vocab_size"], num_units], tf.float32)

	# Transpose to switch to time-major format [max_time, batch, ...]
	src  = tf.nn.embedding_lookup(word_embedding, tf.transpose(features["src"]))
	

	max_time = src.shape[0].value
	


	# --------------------------------------------------------------------------
	# Encoder
	# --------------------------------------------------------------------------
	
	e_cells = []
	for i in range(params["num_layers"]):
		e_cells.append(basic_cell(params,i))

	e_cell = tf.contrib.rnn.MultiRNNCell(e_cells)

	e_initial_state = e_cell.zero_state(params["batch_size"], dtype)

	
	# src = tf.transpose(src, [1, 0, 2])
	# 'outputs' is a tensor of shape [max_time, batch_size, cell.output_size]
	# 'state' is a tensor of shape [batch_size, cell_state_size]

	# inputs [max_time, batch_size, cell_state_size]

	encoder_outputs, encoder_state = tf.nn.dynamic_rnn(
			e_cell,
			src,
			initial_state = e_initial_state,
			dtype=dtype,
			sequence_length=features["src_len"],
			time_major=True,
			swap_memory=True)



	# --------------------------------------------------------------------------
	# Decoder base
	# --------------------------------------------------------------------------
	
	d_cells = []
	for i in range(params["num_layers"]):
		d_cells.append(basic_cell(params,i))

	d_cell = tf.contrib.rnn.MultiRNNCell(d_cells)

	# Time major formatting
	memory = tf.transpose(encoder_outputs, [1, 0, 2])
	
	# set up attention
	attention_mechanism = tf.contrib.seq2seq.LuongAttention(
		num_units, memory, memory_sequence_length=features["src_len"])

	alignment_history = (mode == tf.contrib.learn.ModeKeys.INFER and
						 beam_width == 0)

	d_cell = tf.contrib.seq2seq.AttentionWrapper(
		d_cell,
		attention_mechanism,
		attention_layer_size=num_units,
		alignment_history=alignment_history,
		output_attention=True,
		name="attention")

	d_initial_state = d_cell.zero_state(params["batch_size"], dtype).clone(
		cell_state=encoder_state)

	output_layer = tf.layers.Dense(
		params["vocab_size"], 
		use_bias=False, name="output_projection")

	

	if mode in [tf.estimator.ModeKeys.TRAIN, tf.estimator.ModeKeys.EVAL]:

		tgt_in  = tf.nn.embedding_lookup(word_embedding, tf.transpose(features["tgt_in"]))

		decoder_helper = tf.contrib.seq2seq.TrainingHelper(
				tgt_in, features["tgt_len"],
				time_major=time_major)

		maximum_iterations = None

	else:
		decoder_helper = tf.contrib.seq2seq.GreedyEmbeddingHelper(
			word_embedding,
			tf.fill([params["batch_size"]], vocab_const['tgt_sos_id']), vocab_const['tgt_eos_id'])

		maximum_iterations = tf.round(tf.reduce_max(features["src_len"]) * 2)

	basic_decoder = tf.contrib.seq2seq.BasicDecoder(
			d_cell,
			decoder_helper,
			d_initial_state,)

	
	# 'outputs' is a tensor of shape [max_time, batch_size, cell.output_size]

	final_outputs, final_context_state, _ = tf.contrib.seq2seq.dynamic_decode(
			basic_decoder,
			output_time_major=time_major,
			swap_memory=True,
			maximum_iterations=maximum_iterations)

	logits = output_layer(final_outputs.rnn_output)


	# --------------------------------------------------------------------------
	# Calc loss
	# --------------------------------------------------------------------------

	if mode in [tf.estimator.ModeKeys.TRAIN, tf.estimator.ModeKeys.EVAL]:
	
		# Time major formatting
		labels_t = tf.transpose(labels)
		
		crossent = tf.nn.sparse_softmax_cross_entropy_with_logits(
			labels=labels_t, logits=logits)

		target_weights = tf.sequence_mask(
			features["tgt_len"], max_time, dtype=logits.dtype)

		# Time major formatting
		target_weights = tf.transpose(target_weights)

		loss = tf.reduce_sum(crossent * target_weights) / tf.to_float(params["batch_size"])

	else: 
		loss = None

	# --------------------------------------------------------------------------
	# Optimize
	# --------------------------------------------------------------------------

	if mode == tf.estimator.ModeKeys.TRAIN:

		var = tf.trainable_variables()
		gradients = tf.gradients(loss, var)
		clipped_gradients, _ = tf.clip_by_global_norm(gradients, params["max_gradient_norm"])
		
		optimizer = tf.train.AdamOptimizer(params["learning_rate"])
		train_op = optimizer.apply_gradients(zip(clipped_gradients, var), global_step=tf.train.get_global_step())

	else:
		train_op = None

	# --------------------------------------------------------------------------
	# Eval
	# --------------------------------------------------------------------------
	
	if mode == tf.estimator.ModeKeys.EVAL:

		# Time major formatting
		labels_t = tf.transpose(labels)

		eval_metric_ops = {
			"accuracy": tf.metrics.accuracy(labels_t, tf.argmax(logits, axis=-1)),
		}

	else:
		eval_metric_ops = None

	# --------------------------------------------------------------------------
	# Predictions
	# --------------------------------------------------------------------------

	if mode == tf.estimator.ModeKeys.PREDICT:

		output_ids = tf.argmax(logits, axis=-1)
		vocab_inverse = load_inverse_vocab(params)

		predictions = vocab_inverse.lookup(output_ids)
		

	else:
		predictions = None
	

	return tf.estimator.EstimatorSpec(mode,
		loss=loss,
		train_op=train_op,
		predictions=predictions,
		eval_metric_ops=eval_metric_ops,
		export_outputs=None,
		training_chief_hooks=None,
		training_hooks=None,
		scaffold=None,
		evaluation_hooks=None,
		prediction_hooks=None
	)



	