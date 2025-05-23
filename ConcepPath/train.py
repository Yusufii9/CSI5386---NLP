import json, pickle
from utils.core_utils import *
import random
from torch.cuda.amp import autocast
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader

def to_float32(x):
    return x.type(torch.float32)
    
def train(
    args
):
    """   
        train for a single fold
    """
    
    # ===== 
    fold_name = args.fold_name
    experiment_name = args.experiment_name
    experiment_rp = os.path.join(args.experiment_rp, experiment_name)
    
    output_rps = [os.path.join(experiment_rp, "output", i) for i in ["attn_score", "heatmap", "metrics", "model"]]
    for output_rp in output_rps:
        if not os.path.exists(output_rp):
            os.makedirs(output_rp)
        
    attn_score_fp, _, metric_rp, model_rp = output_rps
    
    slide_prompt_fp = os.path.join(experiment_rp, "input/prompt", "slide_prompts.json")
    patch_prompt_fp = os.path.join(experiment_rp, "input/prompt", "patch_prompts.json")
    
    split_label_fp = os.path.join(experiment_rp, "input/csv/split", fold_name + '.csv')
           
    # input params:
    n_classes = args.n_classes
    feature_rp = args.feature_rp
    lr = args.learning_rate
    vlm_name = args.vlm_name
    # attn_type = args.attn_type
    attn_type = getattr(args, 'attn_type', 'default_value')
    only_learn = getattr(args, 'only_learn', False)
     
    num_epochs = args.num_epochs
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    df_split = pd.read_csv(split_label_fp)
    
    train_data_fps = [os.path.join(feature_rp, i.split("/")[-1].replace(".svs", ".pkl")) for i in list(df_split[df_split["type"]=="train"]["data_path"])]
    train_labels = list(df_split[df_split["type"]=="train"]["label"])

    test_data_fps = [os.path.join(feature_rp, i.split("/")[-1].replace(".svs", ".pkl")) for i in list(df_split[df_split["type"]=="test"]["data_path"])]
    test_labels = list(df_split[df_split["type"]=="test"]["label"])
    
    val_data_fps = [os.path.join(feature_rp, i.split("/")[-1].replace(".svs", ".pkl")) for i in list(df_split[df_split["type"]=="val"]["data_path"])]
    val_labels = list(df_split[df_split["type"]=="val"]["label"])  
      
    print(f"train on {len(train_data_fps)} samples, val on {len(val_data_fps)} samples, test on {len(test_data_fps)} samples")
    
    early_stopping = EarlyStopping(patience = 20, stop_epoch=50, verbose = True)
    
    # =====
    loss_fn = nn.CrossEntropyLoss()
    
    model_dict = {"dropout": 0.1, 'n_classes': 2}
    # =====
    with open(slide_prompt_fp, 'r', encoding='utf-8') as file:
        slide_level_prompts = json.load(file)
        
    slide_level_prompts_ = []
    for slide_level_prompt in sorted(list(slide_level_prompts.keys())):
        slide_level_prompts_.append(f"{slide_level_prompt} {slide_level_prompts[slide_level_prompt]}")
        
    with open(patch_prompt_fp, 'r', encoding='utf-8') as file:
        patch_level_prompts = json.load(file)
        
    patch_level_prompts_ = []
    for patch_level_prompt in sorted(list(patch_level_prompts.keys())):
        for patch_level_prompt_i in patch_level_prompts[patch_level_prompt]:
            patch_level_prompts_.append(f"{patch_level_prompt_i} {patch_level_prompts[patch_level_prompt][patch_level_prompt_i]}")
    
    # if args.only_learn:
    #     len_slide = len(slide_level_prompts_)
    #     len_patch = len(patch_level_prompts_)
        
    #     slide_level_prompts_ = [""]*len_slide
    #     patch_level_prompts_ = [""]*len_patch

    '''
    create vlm_model 
    '''
    
    from trainers import ConcepPath as top
    import open_clip
    clip_model, _, _ = open_clip.create_model_and_transforms('hf-hub:wisdomik/QuiltNet-B-32')
    clip_model = clip_model.to(device)
        
    '''
    creat top model
    '''
    model = top.ConcepPath(
            slide_prompt=slide_level_prompts_,
            patch_prompt=patch_level_prompts_,
            clip_model=clip_model,
            loss_func= loss_fn,
            num_patch_prompt = args.num_patch_prompt,
            n_ctx = args.n_ctx,
            is_shared = args.is_shared,
            n_ddp = args.n_ddp,
            n_classes = args.n_classes,
            orth_ratio = args.orth_ratio,
            # adapt_ratio = args.adapt_ratio
        ).to(device)
    
    '''
    adjust the grad of parameter
    '''
    for name, param in model.named_parameters():
        if "prompt_learner" not in name and "shared_prompt" not in name:
            param.requires_grad_(False)
    
    
    top_transform = transforms.Compose([
        transforms.ToTensor(),
        to_float32
    ])
    
    train_dataset = TOPDataset(train_data_fps, train_labels, top_transform)
    val_dataset = TOPDataset(val_data_fps, val_labels, top_transform)
    test_dataset = TOPDataset(test_data_fps, test_labels, top_transform)
    
    print("Training on {} samples".format(len(train_dataset)))
    print("Validating on {} samples".format(len(val_dataset)))
    print("Testing on {} samples".format(len(test_dataset)))
    
    optimizer = get_optim(model, lr, 5e-4, "adam")
    scheduler = lr_scheduler.StepLR(optimizer, step_size=15, gamma=0.9)
    
    train_loader = DataLoader(train_dataset, shuffle=True)
    val_loader = get_split_loader(val_dataset, )
    test_loader = get_split_loader(test_dataset, )

    # Define a model name (you can customize this)
    model_name = experiment_name  # or any custom string
    
    # Initialize best_metrics with appropriate initial values.
    # For losses (where lower is better), we use float('inf'),
    # and for accuracy and other metrics (where higher is better), we start at 0.
    best_metrics = {
        "train_loss": float('inf'),
        "train_acc": 0.0,
        "train_micro_f1": 0.0,
        "train_macro_f1": 0.0,
        "train_micro_auc": 0.0,
        "train_macro_auc": 0.0,
        "train_avg_sensitivity": 0.0,
        "train_avg_specificity": 0.0,
        "val_loss": float('inf'),
        "val_acc": 0.0,
        "val_micro_f1": 0.0,
        "val_macro_f1": 0.0,
        "val_micro_auc": 0.0,
        "val_macro_auc": 0.0,
        "val_avg_sensitivity": 0.0,
        "val_avg_specificity": 0.0,
        "test_loss": float('inf'),
        "test_acc": 0.0,
        "test_micro_f1": 0.0,
        "test_macro_f1": 0.0,
        "test_micro_auc": 0.0,
        "test_macro_auc": 0.0,
        "test_avg_sensitivity": 0.0,
        "test_avg_specificity": 0.0,
    }

    
    for epoch in range(num_epochs):
        
        # train
        train_loss, train_acc, train_micro_f1, train_macro_f1, train_micro_auc, train_macro_auc, train_avg_sensitivity, train_avg_specificity = train_loop(epoch, model, train_loader, optimizer, n_classes, scheduler, loss_fn)
        
        # val
        stop, val_loss, val_acc, val_micro_f1, val_macro_f1, val_micro_auc, val_macro_auc, val_avg_sensitivity, val_avg_specificity = validate(epoch, model, val_loader, n_classes, early_stopping, loss_fn)
        
        # test
        test_loss, test_acc, test_micro_f1, test_macro_f1, test_micro_auc, test_macro_auc, test_avg_sensitivity, test_avg_specificity, test_patient_results = test(model, test_loader, n_classes, test_data_fps, attn_score_fp, vlm_name, False)
        
        metrics = {
                "train_loss": train_loss,
                "train_acc": train_acc,
                "train_micro_f1": train_micro_f1,
                "train_macro_f1": train_macro_f1,
                "train_micro_auc": train_micro_auc,
                "train_macro_auc": train_macro_auc,
                "train_avg_sensitivity": train_avg_sensitivity,
                "train_avg_specificity": train_avg_specificity,
                
                "val_loss": val_loss,
                "val_acc": val_acc,
                "val_micro_f1": val_micro_f1,
                "val_macro_f1": val_macro_f1,
                "val_micro_auc": val_micro_auc,
                "val_macro_auc": val_macro_auc,
                "val_avg_sensitivity": val_avg_sensitivity,
                "val_avg_specificity": val_avg_specificity,
                
                "test_loss": test_loss,
                "test_acc": test_acc,
                "test_micro_f1": test_micro_f1,
                "test_macro_f1": test_macro_f1,
                "test_micro_auc": test_micro_auc,
                "test_macro_auc": test_macro_auc,
                "test_avg_sensitivity": test_avg_sensitivity,
                "test_avg_specificity": test_avg_specificity,
        }
        
        
        metrics_fp = os.path.join(metric_rp, 'metrics_fold1_5prompts.csv')
        
        for metric in metrics:
            if best_metrics[metric]<metrics[metric]:
                best_metrics[metric]=metrics[metric]
                if metric == "test_micro_auc":
                    torch.save(model.state_dict(), os.path.join(model_rp, f"{model_name}_test_best_micro_auc_model_fold1_5prompts.pt"))
                    update_best_metrics(model_name, best_metrics, metrics_fp)
                elif metric == "test_macro_auc":
                    torch.save(model.state_dict(), os.path.join(model_rp, f"{model_name}_test_best_marco_auc_model_fold1_5prompts.pt"))
                    update_best_metrics(model_name, best_metrics, metrics_fp)
                elif metric == "test_acc":
                    torch.save(model.state_dict(), os.path.join(model_rp, f"{model_name}_test_best_acc_model_fold1_5prompts.pt"))
        
        
        
        
        if args.early_stop and stop:
            break
        


